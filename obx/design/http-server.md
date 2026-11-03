# An HTTP server library for obx

Status: design agreed 2026-11-03 (section 8 records the decisions); S1 starts after the client's H1 merges. Nothing here is built yet. It builds on
stage C1 and C2 of [concurrency.md](concurrency.md) (tasks, `net`,
`blocking` externs, `task.workers`) and on [http-client.md](http-client.md)
(phase H1: the shared request-head parser in `lib/http.obx`; phase H2: TLS
through OpenSSL 3). "Measured here" marks numbers taken on this machine
(Ryzen 7 8745HS, WSL2, the host of PERFORMANCE.md) on 2026-11-03; every
other number comes from the source cited next to it.

## 0. Recommendation

1. **One handler API for HTTP/1.1 and HTTP/2, modelled on Go's net/http.**
   A handler is a plain function value
   `fn(app: &A, rq: mut &http.Request, w: mut &http.Response): Result[Unit, http.Error]`.
   It reads the request (and streams its body) and writes the response the
   same way whatever the protocol. Framing, chunking, keep-alive, HPACK,
   streams and flow control stay inside the library. `A` is the program's
   application state, a copy type made of handles (`postgres.Pool`,
   `task.Shared`), which takes the place of Go's closures and method values
   (obx has no escaping closures or interface values yet).
2. **Blocking-style handlers on tasks, the Loom and Helidon Níma model.**
   HTTP/1.1 runs the handler on the connection's task. HTTP/2 runs one task
   per stream. A handler calls Postgres (asynchronous libpq), SQLite and SQL
   Server (`blocking` externs) as plain calls. A per-route deadline is a
   `task.within` around the handler. A client that goes away cancels the
   handler's task, and with it a Postgres query (`PQcancel`). For SQLite and
   SQL Server, a small runtime hook would let `sqlite3_interrupt` and
   `SQLCancel` do the same (section 2.5).
3. **Routing as in Go 1.22's ServeMux**: `"POST /accounts/{id}/transfers"`,
   `{rest...}`, `{$}`, most specific wins, and conflicts are an `Err` when
   the server starts, not a panic. **Middleware is an explicit chain of plain
   functions** with a `next` value. Per-request "extractors" are ordinary
   calls at the top of a handler (`let user = try auth.user(app, rq)`).
4. **Hardening is the library's main job, and strict is the default.** The
   parser rejects every ambiguous message, with the RFC 9112 rule that
   applies (section 3.1). Every resource has a limit and every wait has a
   deadline, with defaults about as strict as nginx's (section 3.4). The
   HTTP/2 defences against Rapid Reset, CONTINUATION, HPACK bombs, control
   frame floods and MadeYouReset are built in from the first version.
   Parsing code must never panic, because in obx a panic stops the whole
   worker with all its connections. Fuzzing gates that property.
5. **Write HTTP/2 in obx; do not bind nghttp2.** nghttp2 is callback driven
   (26 callback types, 164 entry points). Binding it would push all
   connection state through raw `ptr`s at exactly the boundary where
   ownership should do the checking. The orchestration that matters (tasks
   per stream, flow control that parks, limits) would still have to be
   written in obx. h2o's server-side HTTP/2 is about 4,400 lines of C
   (counted here). An obx version is estimated at 4,000 lines. nghttp2,
   Node's http2, Go and hyper serve as test oracles instead (section 5).
6. **Negotiation**: ALPN `h2` and `http/1.1` over TLS. h2c with prior
   knowledge on plain TCP only when the configuration enables it, for a
   trusted proxy in front. The `Upgrade: h2c` path is **not supported**:
   RFC 9113 section 3.1 deprecated it, and it is a known smuggling vector
   (h2csmuggler).
7. **HTTP/3: not planned.** It needs QUIC (UDP, loss recovery, congestion
   control, the QUIC TLS API that OpenSSL 3.0 lacks). Terminate it in a
   proxy (nginx, h2o, Caddy, a CDN) if it is ever needed.
8. **Phases** (section 7): S1 the HTTP/1.1 server (about 2,600 lines), S2
   TLS and ALPN (about 500), S3 HTTP/2 (about 4,000), S4 extras (SSE helpers,
   static files, compression, WebSockets, cross-worker counters). Each phase
   has a conformance gate and a hardening gate.

## 1. What exists

| Piece | State |
|---|---|
| tasks, scopes, `within`, cancellation, `Shared`, promises | `lib/task.obx` (C1a, C1b) |
| non-blocking TCP; reads and writes that park | `lib/net.obx` (C1b). A `Conn` is owned by one task, with no split into halves |
| `blocking` externs on a helper pool (4 threads by default) | C1c, SPEC 12 |
| worker processes: `task.workers(n, f: &fn(int), w)`, `workersWith(n, WorkerPolicy, f, w)`, `stopWorkers()`, `watchSignals`/`waitSignal`/`terminated`, a supervisor with restarts and drain, a shared listener (`EPOLLEXCLUSIVE`) or `net.listenReusePort` | C2, merged 2026-11-03 |
| Postgres: async libpq, `Pool`/`Lease`, `PQcancel` on cancellation or deadline | `lib/postgres.obx` |
| SQLite and SQL Server: `blocking` externs, not cancellable while a call runs | `lib/sqlite.obx`, `lib/mssql.obx` |
| `cmd/httpd.obx`: the gate server (about 380 lines) | minimal: no timeouts, no limits other than a 4 KiB head, a lenient parser (below) |
| `lib/http.obx` and `lib/url.obx`: the client, with `Headers`, `parseFields`, `parseRequest`, `contentLength` shared with servers | in progress (H1, branch `h1`) |
| TLS (OpenSSL 3.0.13 through externs) | in progress (H2 of the client plan) |

What today's `httpd` gets wrong, as a starting list. It is a gate program, not
a hardened server.

- `Content-Length ` (a space before the colon) is ignored as an unknown
  header, so the request has no body. A front end that accepts the space
  frames it differently: a CL.CL desync (CVE-2019-16276 was this bug in Go).
- With two `Content-Length` lines, the last one wins.
- Any `Transfer-Encoding` gets a 501 and a close. That is safe, but chunked
  bodies cannot be read.
- There is no timeout anywhere. One slowloris connection is held forever,
  and an idle keep-alive connection also stays forever.
- There is no connection limit. `EMFILE` is answered by sleeping 10 ms.
- The accept loop and the loop over pipelined requests can run without
  parking under load. Neither yields, so a flood starves the other tasks
  (section 3.4).

H1's `parseRequest` already fixes the parsing items (strict CRLF, no
obs-fold, no space before a colon, token names, no control characters in
values, a single SP in the request line). Section 3.1 lists what the server
side still has to add.

## 2. The abstraction

### 2.1 Survey

| System | Handler model | Middleware | Concurrency | Protocol difference that leaks to handlers |
|---|---|---|---|---|
| **Go net/http** | `Handler.ServeHTTP(ResponseWriter, *Request)`; ServeMux patterns since 1.22 (`"POST /items/{id}"`, `{x...}`, `{$}`, most specific wins, conflicts panic) | `func(Handler) Handler` wrapping | a goroutine per connection (h1) and per stream (h2); blocking style | `Hijacker` is h1 only; `Pusher` is h2 only and dead (Chrome removed push in 2022); trailers need a declaration or `TrailerPrefix`; full duplex is on in h2 and off in h1 unless `ResponseController.EnableFullDuplex` (1.21); `100 Continue` is sent on the first body read in both |
| Go `http.Server` | `ReadTimeout`, `ReadHeaderTimeout`, `WriteTimeout`, `IdleTimeout` all **0 (none)** by default; `MaxHeaderBytes` 1 MiB | | | `WriteTimeout` is total, so it kills long downloads; 1.20 added per-request deadlines through `ResponseController` |
| **hyper / axum / tower** | `Service::call(Request) -> Future<Response>`; axum handlers are async fns whose arguments are *extractors* (`Path`, `Json`, `State<T>`) | tower `Layer`s wrap services (timeout, limit, trace) | async/await on tokio; h1 by hyper with httparse, h2 by the `h2` crate | few: a body is a stream of frames in both; upgrades through `hyper::upgrade::on` |
| **Netty** | a `ChannelPipeline` of inbound and outbound handlers, events and `ByteBuf` (reference counted) | the pipeline itself | an event loop per thread; callbacks | h2 needs its own codec (`Http2FrameCodec`, a child channel per stream) |
| **Java Loom + Helidon 4 SE (Níma) / Jetty 12** | Níma: a blocking `(req, res) -> res.send(...)` on one virtual thread per connection and per h2 stream, written for virtual threads (no Netty) | filters and services | virtual threads: cheap stackful threads with blocking calls | one routing API for h1 and h2 |
| **Erlang Cowboy** | a process per h1 connection and per h2 stream; handler `init(Req, State)` | stream handlers, middlewares (router, handler) | BEAM processes; let it crash, with supervisors | small; documented limits on everything (below) |
| **nginx / h2o** | C state machines on an event loop; modules or handlers | phases (nginx), handler chain (h2o) | nginx: processes, shared nothing, one loop each; h2o: a loop per thread | handled inside the core; the `limit_*` and `*_timeout` directives are the reference for defaults |
| **Node** | `(req, res)` callbacks, streams; Express `(req, res, next)` | Connect/Express `next()` | one thread, an event loop, llhttp (C, generated) for HTTP/1 | `http2` is a separate core API; its compatibility API imitates `http`'s `req`/`res` |

What carries over to obx:

- **Blocking-style handlers on cheap stackful tasks** (Go, Loom/Níma,
  Cowboy) fit obx exactly. obx tasks are Loom's virtual threads without the
  colouring. A task per h2 stream is what Go, Níma and Cowboy do.
- **Go's two-object API** (read a request, write a response), with the
  status and headers mutable until the first body byte, is the most familiar
  API and the easiest to keep identical across protocols. Go's leaks
  (Hijack, Push, CloseNotifier, the h1-only half duplex) are to be avoided
  from the start (section 2.6).
- **axum's extractors and `State<T>`** show how to get request data and
  shared state without magic. In obx that is a typed `app: &A` parameter
  and ordinary calls that return `Result`.
- **Go's server timeouts all default to zero.** That is the classic
  production mistake. Here every limit and timeout has a non-zero default,
  as in nginx, Cowboy and Node 18+ (Node: `headersTimeout` 60 s,
  `requestTimeout` 300 s, `maxHeaderSize` 16 KiB since CVE-2018-12121).
- **The nginx process model** is already obx's (C2). Its per-connection
  state machines are what obx avoids, by writing handlers in blocking style.

### 2.2 The API

```obx
import "http"
import "net"
import "task"
import "os"
import "postgres"

// The application's state: handles only, so a copy type. Each connection's
// task gets its own copy (a Pool and a Shared are counted handles).
type App = value record {
    db: postgres.Pool
    feed: task.Shared[Feed]          // for server-sent events, below
}

fn routes(): http.Router[App] {
    var r = http.router[App]()
    r.handle("GET /{$}", home)
    r.handle("GET /accounts/{id}", account)                 // GET also answers HEAD
    r.handle("POST /accounts/{id}/transfers", transfer)
    r.handleWith("GET /events", events, http.RouteOpts{deadlineMs: 0, maxBody: 0})   // long-lived
    r.use(http.requestId[App])                              // middleware: plain functions, in order
    r.use(http.accessLog[App])
    r.useUnder("/admin/", requireAdmin)
    return r
}

fn account(app: &App, rq: mut &http.Request, w: mut &http.Response): Result[Unit, http.Error] {
    let id = try rq.paramInt("id")                          // Err: 400, "id" is not an integer
    let c = try http.check(app.db.acquire(), 503)           // a database error becomes a 503
    let rows = try http.check(c.query("SELECT name, balance FROM account WHERE id = $1",
                                      &[postgres.Value.Int(id)]), 500)
    if rows.rowCount() == 0 { return w.fail(404) }          // the library's fixed 404 body
    w.header("Cache-Control", "no-store")
    return w.json(accountJson(&rows))                       // Content-Type, Content-Length, body
}

fn main() {
    var lst = Some(net.listen(":8080").expect("listen"))    // before the fork: one shared accept queue
    let r = task.workersWith(4, http.workerPolicy(), fn(i: int) {
        task.watchSignals(&[task.SIGTERM, task.SIGINT])     // SIGTERM: drain (section 2.7)
        let app = App{db: postgres.newPool(os.envOr("DSN", ""), 8), feed: newFeed()}
        var cfg = http.defaults()                           // every limit and timeout has a value
        cfg.maxConns = 10000
        match http.serve(replace(mut &lst, None).unwrap(), app, routes(), cfg) {
            Ok(st) { println("worker " + intToStr(i) + ": " + st.toStr()) }
            Err(e) { eprintln("worker " + intToStr(i) + ": " + e.toStr()); os.quit(1) }
        }
    })
    ...
}
```

The types, in outline:

```obx
pub type Handler[A] = fn(&A, mut &Request, mut &Response): Result[Unit, Error]
pub type Middleware[A] = fn(&A, mut &Request, mut &Response, Next[A]): Result[Unit, Error]

pub type Request = record {
    pub method: str            // as sent, a token
    pub path: str              // the decoded path (section 3.2); rq.rawTarget() gives it as sent
    pub host: str              // from Host, or :authority, validated, lowercase
    pub proto: str             // "HTTP/1.1" or "HTTP/2": for logs only, no behaviour depends on it
    pub headers: Headers       // lib/http.obx's type, shared with the client
    pub peer: str              // the TCP peer ("10.0.0.7:51234"); see trustedProxies for X-Forwarded-For
    pub id: str                // the request id (section 3.7)
    ...                        // private: route params, the body's port (h1 or h2), limits, deadline
}

pub fn (rq: &Request) param(name: str): str                       // a {name} of the pattern, decoded
pub fn (rq: &Request) paramInt(name: str): Result[int, Error]     // 400 when not a decimal integer
pub fn (rq: &Request) query(name: str): Option[str]               // the first value; at most cfg.maxParams
pub fn (rq: &Request) header(name: str): Option[str]
pub fn (rq: mut &Request) read(buf: mut &[byte], at: Where): Result[int, Error]   // streams; 0 at the end
pub fn (rq: mut &Request) bytes(limit: int, at: Where): Result[[]byte, Error]      // the whole body, 413 past limit
pub fn (rq: mut &Request) text(limit: int, at: Where): Result[str, Error]          // same, UTF-8 checked
pub fn (rq: &Request) trailers(): &Headers        // after the body is read; empty unless enabled

pub type Response = record { ... }   // private: status, headers, the port, what has been written
pub fn (w: mut &Response) status(code: int)                  // before the head is written
pub fn (w: mut &Response) header(name: str, value: str)      // validated (section 3.3); a bad one fails the response
pub fn (w: mut &Response) write(data: &[byte], at: Where): Result[Unit, Error]  // parks while the peer is slow
pub fn (w: mut &Response) writeStr(s: str, at: Where): Result[Unit, Error]
pub fn (w: mut &Response) flush(at: Where): Result[Unit, Error]  // send what is buffered now (h1 chunk, h2 DATA)
pub fn (w: mut &Response) trailer(name: str, value: str)     // declared before the head; sent at the end
pub fn (w: mut &Response) text(s: str, at: Where): Result[Unit, Error]   // status 200 unless set; Content-Length
pub fn (w: mut &Response) json(s: str, at: Where): Result[Unit, Error]
pub fn (w: mut &Response) fail(code: int, at: Where): Result[Unit, Error] // a fixed body, nothing from input
pub fn (w: mut &Response) redirect(code: int, location: str, at: Where): Result[Unit, Error]  // relative only by default
pub fn (w: mut &Response) startEvents(at: Where): Result[Unit, Error]    // SSE: the head, no-store, flushed
pub fn (w: mut &Response) event(name: str, data: str, at: Where): Result[Unit, Error]  // write and flush one event

pub type Error = record {
    pub status: int            // the status to answer with, if nothing has been written yet
    pub kind: ErrorKind        // Client, Server, Gone (peer closed), Timeout, Limit, Protocol, Config
    pub msg: str               // for the log, with the request id; never sent to the client
}
pub fn check[T, E: Show](r: Result[T, E], status: int): Result[T, Error]   // any error as an http.Error
```

(`at` is the `Where` parameter, which a call leaves out. Every call that can
park takes one, as in `net` and `postgres`, so a trap names the handler's
line.)

**Ownership of the connection.** A borrow cannot be stored in a record, so
`Request` and `Response` cannot hold `&net.Conn`. Each owns its half of the
connection while the handler runs, and the server takes both back
afterwards:

- HTTP/1.1: `net.Conn.split()` gives an owned `ReadHalf` and `WriteHalf`
  (new in `net`; the descriptor closes when both are dropped). The request
  owns the read half and the connection's read buffer. The response owns
  the write half. After the handler returns, the server moves them out
  again (`replace`) and reads the next request.
- HTTP/2: the request owns a stream inbox and the response a stream outbox.
  Both are handles to the connection's `Shared` state (section 4).

The handler's signature is the same either way, and the protocol is a
private closed type inside each object.

**Why the response is written through methods, not returned.** Returning a
`Response` value (axum, Helidon's `res.send`) is neat for small replies, but
it fits streaming, flushing, server-sent events and backpressure poorly. Go's
writer handles all of them with one model. Helpers (`text`, `json`, `fail`)
make the small case one line.

### 2.3 Routing

Go 1.22's pattern language ([go.dev/blog/routing-enhancements](https://go.dev/blog/routing-enhancements)),
adopted with three changes:

- Syntax `[METHOD ][HOST]/[PATH]`. `{name}` matches one segment, `{name...}`
  the rest, and `{$}` the end only. `GET` also matches `HEAD`, and the body
  is not sent for HEAD.
- Precedence: the most specific pattern wins. Two patterns that match the
  same requests with neither more specific are a conflict. **obx difference:**
  Go panics at registration, while `http.serve` returns `Err(Error{kind:
  Config})` listing every conflict before it accepts a connection.
- A path that matches with the wrong method gets a 405 with `Allow` (as Go
  does). A subtree pattern `/dir/` redirects `/dir` with a 301 (as Go does).
- **obx difference:** Go cleans the path and redirects (`/a/../b` to `/b`).
  obx answers 400 for dot segments, raw or encoded, and for encoded `/` and
  NUL. It does not collapse `//`: a path with an empty segment matches only
  a pattern that has one. Section 3.2 gives the reasoning: proxy and server
  must not disagree about which path was asked for.
- Matching is on the raw path split at raw `/`, and each parameter is
  decoded after the split (Go 1.22 does the same), so `%2F` inside a segment
  never creates a new segment. A parameter that decodes to invalid UTF-8 is
  a 400.

The table is a segment trie (Go's `routing_tree.go`, 242 lines), built once
per worker and immutable afterwards. Connection tasks cannot borrow it from
`serve`'s frame (SPEC 16: a task gets only owned arguments). So the router
lives in a `task.Shared`, and a lookup copies out the matched handler (a
`fn` value, which is copy) and the parameters inside one `with`, which does
not park. A read-only shared handle would make this cheaper (section 8, Q6).

### 2.4 Middleware and request-scoped state

```obx
fn requireAdmin(app: &App, rq: mut &http.Request, w: mut &http.Response,
                next: http.Next[App]): Result[Unit, http.Error] {
    match rq.header("Authorization") {
        Some(v) { if isAdminToken(app, v) { return next.run(app, rq, w) } }
        None {}
    }
    w.header("WWW-Authenticate", "Bearer")
    return w.fail(401)
}
```

- `Next[A]` is a copy value (the router's handle, the route, the position).
  `next.run` looks up the next function inside a `with` and calls it outside
  the `with`. Everything is a plain function value, so nothing is captured
  and nothing is hidden: each middleware decides whether to call `next`,
  runs code before and after, and sees the handler's `Result`.
- **Built into the server, not middleware:** limits, timeouts, the
  per-route deadline, panic containment (which cannot be middleware, below),
  error responses and the default response headers. These must hold even
  when a program's chain is wrong, so they are configuration (`http.Config`,
  `RouteOpts`).
- **Library middleware**: `requestId`, `accessLog`, `securityHeaders`,
  `basicLimits` (per-IP request rate within the worker), `cors` (explicit
  origins only), `compress` (S4).
- **Request-scoped state** is the handler's own locals. What a middleware
  finds out (the user, the tenant) is better got by an explicit call
  (`let user = try auth.user(app, rq)`, axum's extractor as a function)
  than through an untyped bag like Go's `context.Value`. For the access log,
  `rq.note(key, value)` adds a field (escaped, section 3.7).
- **Errors**: a handler returns `Err(http.Error)`. If the head is not yet
  written, the server sends `status` with its fixed body, logs `msg` with
  the request id, and keeps the connection (unless the request body is
  unread and too large to drain). If the head is written, the response is
  aborted: HTTP/1.1 closes the connection without the final chunk (or short
  of `Content-Length`), so the client sees a truncated message; HTTP/2 sends
  `RST_STREAM(INTERNAL_ERROR)`. `try` needs one error type, so database
  errors go through `http.check(r, status)` until `try` can convert errors
  (section 8, Q2).

### 2.5 Deadlines, cancellation and databases

- **The route's deadline.** The server runs the middleware chain and the
  handler inside `task.within(route.deadlineMs, ...)`, 30 s by default.
  Every call that can park inside the handler (net, postgres, pool waits,
  `sleep`, futures) then returns `Timeout` once it has passed. The handler
  unwinds through its `Result`s, and the server answers 503 if nothing was
  written (504 is for gateways). `deadlineMs: 0` means none (SSE, long
  downloads). Those routes are bounded by the write progress timeout
  instead (section 3.4). The handler may tighten its own deadline with a
  nested `within`.
- **Client gone.** HTTP/2: `RST_STREAM` or the end of the connection cancels
  the stream's task (its future is dropped). HTTP/1.1: while the handler
  runs, nobody reads the socket, so the server asks the runtime to cancel
  the connection's task if the peer hangs up. This is a new runtime
  registration, `EPOLLRDHUP` on the descriptor, with a guard value whose
  drop disarms it; it is not a read. Go's background read does the same,
  and so it also cancels a request whose client half-closes after sending.
  That is legal but rare, and the same trade-off is accepted here
  (`Config.cancelOnHalfClose` turns it off). The handler's next park returns
  `Cancelled`, and its writes return `EPIPE`.
- **What it means for a query in flight:**

  | Database | Today | With the proposed hook |
  |---|---|---|
  | Postgres (async libpq) | cancelled: `PQcancel` on cancellation or deadline, the results drained (at most 5 s), the connection back in the pool | same |
  | SQLite (`sqlite3_step` on a helper thread) | the running step finishes; the task sees the cancellation at its next step, so a scan stops between rows and one long step does not | `sqlite3_interrupt(db)`, which SQLite documents as safe to call from another thread, called on the program's thread when the task is cancelled; the step returns `SQLITE_INTERRUPT` |
  | SQL Server (ODBC on a helper thread) | the call runs to completion; the helper is held meanwhile | `SQLCancel(hstmt)` from the program's thread (ODBC 3 allows it for a statement running synchronously on another thread) |

  The hook is a runtime addition with no syntax:
  `task.onCancelCall(f: extern fn(ptr), arg: ptr)` registers a C function
  that the runtime calls on the program's thread if the task is cancelled,
  or its deadline passes, while it is parked in a blocking call. The C
  function must be one that is safe to call concurrently with the running
  call. No obx code runs on a helper thread. Section 8, Q8.
- **The helper pool is per worker, and it is small** (4 threads by default).
  SQL Server queries and long SQLite steps hold a helper each, and name
  lookups queue behind them. So the server keeps a per-worker cap on
  requests inside blocking database calls (`Config.maxBlocking`, default
  helpers − 1), and `OBX_HELPERS` should be at least the SQL Server pool
  size + 2.

### 2.6 One API, two protocols: where Go leaks and what obx does

| Go | The leak | obx |
|---|---|---|
| `Hijacker` (h1 only) | a handler that takes the socket breaks under h2 | no hijack. WebSockets come through one function, `websocket.accept(rq, w)` (S4), which does `Upgrade` on h1 and extended CONNECT (RFC 8441) on h2 |
| `Pusher` (h2 only) | server push is deprecated; browsers dropped it | not implemented; `SETTINGS_ENABLE_PUSH` is ignored. `103 Early Hints` (RFC 8297) through `w.hint(...)` works on both |
| `Flusher` | works on both | `w.flush()`: a chunk on h1, DATA frames on h2 |
| Trailers: declared with `Trailer:` or `TrailerPrefix`; need chunked on h1 | the handler must know to declare them | `w.trailer(...)` before the head; the library declares them and uses chunked on h1, a final HEADERS frame on h2 |
| Full duplex: on in h2, off in h1 unless `EnableFullDuplex` | reading the body after writing behaves differently | off on both by default: once the head is written, an unread body is discarded up to `maxDrain` (256 KiB, Go's `maxPostHandlerReadBytes`), past that h1 closes and h2 sends `RST_STREAM(NO_ERROR)`. `w.fullDuplex()` turns it on for both |
| `Expect: 100-continue`: sent on the first body read | same in both | same in both, as in Go (section 3.1) |
| `Request.Proto`, `Connection` headers | handlers sometimes branch on them | `proto` is for logs only; connection-specific headers are removed on h1 and rejected on h2 (RFC 9113 section 8.2.2) |
| `CloseNotifier` (deprecated) | | cancellation (section 2.5) |

### 2.7 Panics, workers and shutdown

- obx does not unwind, so a panic, an index out of range, an overflow, a
  stack overflow or out of memory stops the **worker** with every
  connection it holds (SPEC 14; concurrency.md, Q5). Bounds checks make a
  parser bug a denial of service rather than code execution, but the
  service lost is a whole worker. Consequences:
  - The library's own code must not panic on any input: limits are checked
    before any arithmetic (`checkedAdd` and similar), no `unwrap` or
    `expect` touches a value derived from input, and recursion has a depth
    limit. Gate: the fuzzers (section 5) see no exit other than 0, and a
    lint finds no `unwrap`/`expect` in `lib/http*.obx` outside an allow
    list.
  - "Recovery middleware" cannot exist. What exists instead: several
    workers (two at least, even on one core), the shared listener (the
    accept queue survives a killed worker; C2's kill test lost exactly that
    worker's connections), and the supervisor's restart (about 105 ms in
    C2's test).
  - **Query of death.** C2's default policy gives up after 5 crashes in
    10 s and then stops the whole server. A hostile request that triggers a
    handler panic could use this to stop the server with six requests.
    `http.workerPolicy()` therefore should count only crashes before the
    worker became ready (a bad configuration) toward giving up. After that
    it should restart with backoff indefinitely. That needs a "ready" mark
    in C2 (`task.workerReady()`, like systemd's `READY=1`), a small addition.
  - **A black box** (S4): each worker writes the request it is running
    (id, method, route pattern, peer) into its slot of a `MAP_SHARED` page
    made before the fork. When the worker dies, the supervisor logs the
    slot. The request that killed it is then known without any code running
    in the dying process.
- **Graceful drain** on `SIGTERM` (C2 forwards it to every worker), with
  `serve` watching `task.terminated()`:
  1. Stop accepting, and close idle keep-alive connections.
  2. On HTTP/1.1, put `Connection: close` on the remaining responses.
  3. On HTTP/2, send a GOAWAY with the last stream id `2^31-1`, then after a
     round trip (a PING) the real last id (RFC 9113 section 6.8).
  4. Wait up to `drainMs` (30 s by default) for the requests in flight.
  5. Cancel what remains: the handlers see `Cancelled`, and Postgres
     queries are cancelled on the server.

  `serve` then returns its statistics, and the worker returns (counted as
  drained).

### 2.8 Streaming, long responses and server-sent events

```obx
fn events(app: &App, rq: mut &http.Request, w: mut &http.Response): Result[Unit, http.Error] {
    try w.startEvents()                                 // 200, text/event-stream, no-store; head flushed
    var seen = 0
    while true {
        let ev = try http.check(nextEvent(app.feed, seen), 503)   // parks on a promise for the next event
        try w.event("price", ev.data)                   // parks while the client reads slowly
        seen = ev.seq
    }
}
```

- `nextEvent` is a broadcast hub: a `Shared[Feed]` with the recent events
  and a list of waiting promises. The publisher completes them outside the
  `with`. This is the pattern of `postgres.Pool`'s waiters.
- Backpressure is automatic. `write` parks when the socket buffer is full
  (h1) or the stream's flow-control window is used up (h2). A client that
  stops reading is caught by the progress timeout, not by a total deadline.
- A keep-alive comment (`:\n\n`) every `sseKeepAliveMs` (15 s) lets
  proxies and the hangup detection see a dead client.
- A request body streams the same way (`rq.read(mut &buf)`), with the
  route's `maxBody` counted as it arrives.

## 3. Hardening

Each subsection lists the attack or failure, the defence and the default,
and says whether obx's design helps. "Default" is what `http.defaults()`
gives. All are configurable, and several can also be set per route.

### 3.1 Parsing HTTP/1.1: smuggling and desync

References: RFC 9112 sections 2.2, 3, 5, 6, 7 and 11.2; RFC 9110 sections
5.4–5.6, 8.6 and 10.1.1. Watchfire's "HTTP Request Smuggling" (2005). James
Kettle (PortSwigger): "HTTP Desync Attacks: Request Smuggling Reborn" (2019),
"HTTP/2: The Sequel is Always Worse" (2021), "Browser-Powered Desync
Attacks" (2022, CL.0 and client-side desync), "HTTP/1.1 Must Die: the Desync
Endgame" (2025: 0.CL and Expect-based desyncs). Papers: T-Reqs (Jabiyev et
al., ACM CCS 2021), Frameshifter (USENIX Security 2022), the HTTP Garden
(Kallus et al., arXiv:2405.17737, 2024). CWE-444.

A desync needs two parsers that disagree about where a message ends: a
proxy and the origin. obx cannot fix the proxy, but it can refuse anything
that two reasonable parsers could read differently, and close the
connection after refusing. **Rule: reject, never repair**, and close after
any framing error (RFC 9112 section 6.3 requires the close for most of
them).

| Case | Rule (RFC) | obx server |
|---|---|---|
| `Content-Length` and `Transfer-Encoding` both (CL.TE, TE.CL) | TE wins, and the server MAY reject; it must close afterwards (9112 §6.1, §6.3) | 400 and close |
| two `Content-Length` lines, or a list `5, 5` | identical values may be merged (9110 §8.6) | 400 and close for any repetition, even identical. H1's `contentLength` accepts identical lists for the client; the server needs a strict mode |
| `Content-Length` not plain digits (`+5`, ` 5`, `0x5`, `5 `, empty, longer than 18 digits) | invalid: 400 and close (9112 §6.3) | same; the value is parsed without overflow |
| `Transfer-Encoding` other than exactly `chunked` (`chunked, identity`, `xchunked`, `gzip, chunked`, `chunked` twice, a second TE line, a TE line folded onto the next) — TE.TE | chunked must be last; unknown codings get 501 (9112 §6.1) | `chunked` alone is accepted (case-insensitive, no other coding, one line); `gzip, chunked` gets 501; anything else 400. Close in every case |
| `Transfer-Encoding` in an HTTP/1.0 request | framing faulty; close after (9112 §6.1) | 400 and close |
| whitespace between a field name and the colon | MUST reject with 400 (9112 §5.1) | 400 (H1's `parseFields` already does: the name is not a token) |
| obs-fold (a line starting with SP or HTAB) | reject or replace with SP (9112 §5.2) | 400 |
| a bare LF as a line end; a bare CR anywhere | LF alone MAY be accepted; a bare CR MUST be rejected or replaced (9112 §2.2) | both rejected with 400 (H1's `headEnd` returns −2 on a bare LF) |
| whitespace between the request line and the first header | reject, or ignore those lines (9112 §2.2) | 400 |
| CR, LF or NUL in a field value; other controls | MUST reject or replace with SP (9110 §5.5) | 400 for every control byte except HTAB |
| header names not tokens; empty names | 9110 §5.6.2 | 400 |
| request line: more than one SP, HTAB, no version (HTTP/0.9) | 9112 §3 | 400 and close; HTTP/0.9 is never spoken |
| version `HTTP/1.2` … `HTTP/1.9` | a higher minor version is handled as the highest one implemented (9110 §2.5) | handled as HTTP/1.1. `HTTP/2.0` on the HTTP/1 path is a 505, except the h2c preface when enabled (section 4.4). Other versions get a 505 |
| target forms | origin-form; absolute-form must be accepted; authority-form only for CONNECT; asterisk-form only for OPTIONS (9112 §3.2) | origin-form; absolute-form with an `http`/`https` scheme, whose authority replaces Host (9112 §3.2.2), and 400 if Host differs; CONNECT gets 405 (an origin server); `OPTIONS *` is answered by the library |
| Host missing, repeated or invalid in HTTP/1.1 | MUST answer 400 (9112 §3.2) | 400; Host is checked as an RFC 3986 reg-name or IP literal with an optional port, then lowercased. `Config.hosts` (a list) turns unknown hosts away with 421, which defends against DNS rebinding |
| chunk size: not hex, empty, leading or trailing whitespace, overflow | 9112 §7.1 | 400 and close. At most 16 hex digits, accumulated with `checkedMul`/`checkedAdd`, and checked against the route's `maxBody` before any byte is read |
| chunk extensions (`;a=b`) | ignored, but a server ought to limit them (9112 §7.1.1) | ignored after validation (token, then `=` token or quoted string); at most 4 KiB per chunk line and 16 KiB per message (Node CVE-2024-22019 and Go CVE-2023-39326 were unbounded chunk overhead) |
| a bare LF or other garbage after chunk data | 9112 §7.1 | 400 and close |
| trailers | must not be merged into the header section unless they are known to be safe (9110 §6.5.1) | read with the head's rules, at most 8 KiB and 32 lines, and discarded unless the route asks for them (`rq.trailers()`); never merged |
| `Expect: 100-continue` | send 100 or a final status; ignore it in HTTP/1.0 (9110 §10.1.1) | 100 is sent on the first `read`. If the handler answers without reading, the connection is closed after the response, unless the body is at most `maxDrain` and arrives within the head timeout. Any other `Expect` value gets a 417. Kettle's 2025 Expect desyncs need a front end that mishandles this; the origin must not add a second interpretation |
| a request body on GET/HEAD/DELETE | allowed but without defined semantics | read and counted against `maxBody` like any other, so framing stays exact (a CL.0 desync comes from a server that ignores a body) |
| pipelined requests | answered in order (9112 §9.3.2) | one at a time: the next request is not parsed until this response is written, so a pipelining client gets backpressure from the socket |
| leading empty lines before a request | SHOULD ignore at least one (9112 §2.2) | at most 2 CRLFs are skipped |

Does obx help? Partly. Bounds-checked slices and `Result` make each check
explicit and every error a value. But strictness is a policy: the parser
must be written to it and checked against differential fuzzing (section
5.2).

### 3.2 Targets, paths and Host

| Attack | Reference | Defence and default |
|---|---|---|
| traversal by dot segments, raw or encoded (`/../`, `%2e%2e`, `.%2e`), double decoding (`%252e`) | Apache CVE-2021-41773 and CVE-2021-42013; IIS CVE-2001-0333 (double decoding); CWE-22 | the path is split at raw `/`, and each segment is percent-decoded **once**. A segment that is `.` or `..` before or after decoding is a 400. `%25` decodes to `%` and is not decoded again |
| encoded slash `%2F` and backslash | Orange Tsai, "Breaking Parser Logic" (Black Hat USA 2018) | `%2F` stays inside its segment (matching works on raw segments); a route that serves files refuses it in a parameter. `\` is allowed only in a decoded parameter; the file server refuses it |
| NUL (`%00`), control characters | | 400 when decoded |
| overlong or invalid UTF-8 (`%c0%af`) | IIS CVE-2000-0884 | a decoded parameter or path must be valid, shortest-form UTF-8 (the strict decoder that `mssql` already has); otherwise 400 |
| `//` and empty segments | proxies collapse them differently from servers | not collapsed; the path matches only a pattern with an empty segment, so it is usually a 404 |
| a very long target | 414 (RFC 9110 §15.5.15) | the request line is at most 8 KiB, otherwise 414 |
| query strings: many parameters; hash flooding | Klink and Wälde, 28C3 2011; PHP CVE-2011-4885 | at most `maxParams` (1,000) query and form parameters, otherwise 400. obx's `std.Map` hashes `str` with **unseeded FNV-1a**, so keys chosen to collide in a map's index bits are cheap to find. The library keeps parameters in a list scanned linearly (bounded by `maxParams`), never in a `Map`; and std's string hash should get a per-process random seed (section 8, Q7) |
| Host header poisoning (password-reset links, cache keys) | Kettle, "Practical HTTP Host header attacks" (2013) | `rq.host` is validated. Code that builds absolute URLs uses `Config.publicOrigin`, never the request's Host |
| X-Forwarded-For and Forwarded spoofing | RFC 7239 | ignored unless the peer is in `Config.trustedProxies`; then the rightmost untrusted address is taken |
| open redirects | CWE-601 | `w.redirect` accepts only a relative path starting with a single `/`. It refuses `//host`, `/\host` and control characters unless a host is in `Config.redirectHosts` |
| static files: symlinks and `..` from a parameter | | S4's file server opens with `openat2(RESOLVE_BENEATH \| RESOLVE_NO_SYMLINKS)` (Linux 5.6+) under a root descriptor, so the kernel enforces the confinement whatever the string check missed |

### 3.3 Responses built from input

| Problem | Reference | Defence |
|---|---|---|
| header injection and response splitting (CR/LF in a header value built from input) | Amit Klein, "Divide and Conquer" (2004); RFC 9112 §11.1; CWE-113 | `w.header` checks the name (a token) and the value (no CR, LF, NUL or other control except HTAB). A bad one marks the response failed: the head is not written, the client gets a 500, and the log gets the header name. Fail closed, never strip: Go replaces newlines with spaces on h1, which hides the bug |
| the status line | | the reason phrase is the library's fixed text for the code; the code must be 100–599 |
| content sniffing | MIME sniffing makes uploaded data executable as HTML | `X-Content-Type-Options: nosniff` on every response. A response without a `Content-Type` gets `application/octet-stream`. There is no `DetectContentType` (Go sniffs; obx deliberately does not) |
| error bodies | leaking stack traces, SQL, paths | `w.fail(code)` and the server's own errors send a fixed short text per status plus the request id; `Error.msg` goes to the log only |
| security headers | OWASP Secure Headers Project | `http.securityHeaders` middleware (opt-in, for HTML apps): `Content-Security-Policy` (default `default-src 'self'; frame-ancestors 'none'`), `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, HSTS on TLS when configured. Always on: `nosniff`, and `Cache-Control: no-store` on error responses. `Date` is always sent (RFC 9110 §6.6.1) and cached per second. No `Server` header by default |
| cookies | | a `Cookie` type with `Secure`, `HttpOnly` and `SameSite=Lax` as defaults; values encoded, names checked as tokens |
| HTML built from input | XSS | out of scope for the server; a later template feature (SPEC 13) should escape by context |

### 3.4 Resource exhaustion

| Attack | Reference | Defence | Default |
|---|---|---|---|
| **slowloris** (a head sent slowly) | R. Hansen, 2009; CVE-2007-6750 | a deadline on the whole head, counted from its first byte, not per read (a per-read timeout lets a byte every 9 s through) | `headerTimeoutMs` 10,000 → 408 and close |
| slow POST (R-U-Dead-Yet: a body sent slowly) | Wong Onn Chee and T. Brennan, OWASP 2010 | a minimum rate after a grace period, as in Apache's `mod_reqtimeout` (`body=20,MinRate=500`) | `bodyGraceMs` 10,000, then at least `bodyMinRate` 1,024 bytes/s averaged over 5 s → 408 and close |
| **slow read** (the client does not read the response; with h2, a zero window) | S. Shekyan, slowhttptest (2012); CVE-2019-9511/9517 for h2 | `write` parks; a write that makes no progress for `writeStallMs` fails the response and closes. No total write deadline, so long downloads work (Go's `WriteTimeout` is total) | `writeStallMs` 30,000 |
| idle keep-alive connections | | an idle timeout that shrinks under pressure: at 80 % of `maxConns` the oldest idle connections are closed first | `idleTimeoutMs` 60,000 (nginx 75 s, Cowboy 60 s); 5,000 under pressure |
| too many requests on one connection | | `Connection: close` after `maxRequestsPerConn` | 1,000 (nginx's `keepalive_requests`, Cowboy's `max_keepalive`) |
| connections per worker | fd exhaustion; the task-stack mappings (below) | at `maxConns` the accept loop stops accepting; new connections wait in the kernel's queue (backlog 4,096, then SYN cookies) | 10,000 per worker |
| connections per client IP | | counted per worker (shared nothing); across workers needs a shared counter area (S4) | `maxConnsPerIp` 0 = off; 256 suggested behind no proxy |
| `EMFILE`/`ENFILE` on accept | the busy loop on a refused accept (libev, nginx) | keep `maxConns` + the database and file budget below `RLIMIT_NOFILE`, checked at start (`serve` returns a `Config` error otherwise); a reserved spare descriptor is closed to accept and immediately close one connection, so the queue drains instead of spinning | margin 64 |
| **accept floods** starving other tasks | obx-specific | `accept` does not park while connections are queued, and `spawn` does not park either, so the loop must `task.yield()` every 16 accepts. Linux `TCP_DEFER_ACCEPT` (= the head timeout) means a connection that sends nothing never costs a task | yield every 16; `TCP_DEFER_ACCEPT` on |
| **a busy connection starving others** | obx-specific: cooperative scheduling | a read that finds data does not park, so a client that pipelines without pause, or a fast body upload, would never let other tasks run. The connection loop yields after each request, and body copies yield every 64 KiB | |
| head size and header count | 9110 §5.4: answer with a 4xx | head at most 16 KiB, at most 100 lines, a line at most 8 KiB → 431 (RFC 6585) and close. The read buffer starts at 4 KiB and grows only while a head is incomplete; it shrinks back after the request. Go allows 1 MiB, Node 16 KiB, nginx 1 KiB + 4 × 8 KiB, Apache 100 × 8,190 bytes | `maxHeadBytes` 16,384; `maxHeaders` 100 |
| URI length | | 8 KiB → 414 | `maxTargetBytes` 8,192 |
| body size | | per route; `bytes`/`text` take a limit no larger than the route's; streaming counts as it goes; a declared `Content-Length` over the limit is refused **before** reading (413, then close, since the body is not drained) | `maxBody` 1 MiB (nginx's `client_max_body_size`) |
| memory per worker in total | out of memory stops the worker (exit 2) with all its connections | the server counts buffered bytes (head buffers, buffered bodies, response buffers); above `memBudget` new requests get 503 and the largest idle buffers are released | `memBudget` 512 MiB per worker |
| tasks per connection | | h1: 1 (the connection's); h2: at most `maxConcurrentStreams` + 1 (section 4.2) | 100 + 1 |
| decompression bombs (if `Content-Encoding: gzip` request bodies are accepted) | CWE-409 | off by default (415). When enabled per route: zlib on the program's thread, output counted against `maxBody`, and a ratio limit | ratio 100:1; output ≤ `maxBody` |
| deep nesting in parsers (JSON, forms) | stack overflow (exit 4) stops the worker | a depth limit in every recursive parser the library ships | depth 64 |
| CPU-heavy requests (regexes, big decodes) | ReDoS | `regex` is planned as linear-time (design/regex.md, Go's design), so no backtracking blowup. Long CPU work belongs on a helper, in another process, or yields | |
| task stacks | each task is an `mmap` with a guard page, about two mappings | 10,000 connections → about 20,000 mappings. `vm.max_map_count` is 65,530 on many stock kernels (1,048,576 here), so about 32,000 tasks per worker is the ceiling there; `serve` checks the setting against `maxConns` at start | |

**Memory per connection.** Measured here: an idle h1 connection costs 12.7
KB of RSS in today's httpd, 15.9 KB in hyper and 20.6 KB in Go (section 6).
The library should stay under 16 KB: two stack pages, a 4 KiB read buffer
released while idle, and the request and response records reused across
requests. Running idle connections without a stack would save more, but it
is a runtime change (concurrency.md section 7) and is not needed for these
targets.

Does obx help? Yes, for the main slowloris cost. A slow connection costs a
parked task (about 13 KB), not a thread, so the attack needs tens of
thousands of sockets instead of a few hundred (as against Apache prefork).
The timeouts are still needed to free the sockets. Cooperative scheduling
works the other way: obx needs explicit yields where Go and the JVM preempt.
Shared nothing makes per-IP limits per worker unless a shared counter area
is added.

### 3.5 HTTP/2

References: RFC 9113 sections 5.1.2, 5.2, 6, 8.1.1, 8.2, 10.5 and 10.5.1;
RFC 7541 sections 4.2, 5.1, 5.2, 6.3 and 7.3; Netflix's 2019 advisory
(CVE-2019-9511 to 9518); Imperva, "HTTP/2: In-depth analysis of the top four
flaws" (2016); CVE-2023-44487 (Rapid Reset, October 2023; Go's is
CVE-2023-39325); the CONTINUATION flood (Bartek Nowotarski, April 2024, CERT
VU#421644: CVE-2024-27316 Apache httpd, CVE-2024-27983 Node, CVE-2023-45288
Go, CVE-2024-28182 nghttp2, CVE-2024-27919 Envoy); MadeYouReset
(CVE-2025-8671, VU#767506, August 2025); Go CVE-2022-41717 (HPACK
canonical-header cache growth).

| Attack | Defence | Default |
|---|---|---|
| **Rapid Reset** (open a stream, then `RST_STREAM` at once, repeated: the stream limit never bites because each stream is "closed") | a stream's handler counts against the concurrency limit **until its task has ended**, not until the client resets it. HEADERS beyond the limit are refused with `RST_STREAM(REFUSED_STREAM)`, as long as refusals stay under a budget. Past the budget, `GOAWAY(ENHANCE_YOUR_CALM)` and close. Go's fix is the same idea (handlers started at most at the stream limit, at most 4× that waiting) | streams 100; at most 200 client resets of open streams per 10 s, and no more than half the streams of a connection after its first 100 |
| **MadeYouReset** (the client provokes the *server* into resetting streams by sending malformed frames: WINDOW_UPDATE 0, DATA on a half-closed stream) | server-sent resets caused by client errors count in the same budget | same budget |
| **CONTINUATION flood** (HEADERS without END_HEADERS, then CONTINUATION frames without end; servers buffered or decoded without limit) | the header block size is counted across HEADERS and every CONTINUATION, compressed and decoded. Past the limit: `GOAWAY(ENHANCE_YOUR_CALM)` and close, with nothing more read (the HPACK state can no longer be trusted). An incomplete block must complete within the head timeout | `SETTINGS_MAX_HEADER_LIST_SIZE` 16 KiB decoded; at most 32 KiB compressed per block |
| **HPACK bomb** (a large dynamic entry referenced many times, so a small frame decodes to megabytes) | the decoded size (name + value + 32 per field, RFC 7541 §4.1) is counted while decoding and stopped at the limit above. The dynamic table stays at 4,096 bytes (`SETTINGS_HEADER_TABLE_SIZE`). Size updates come only at the start of a block and never exceed the setting (§6.3) | table 4 KiB; list 16 KiB |
| HPACK decoding errors | integers longer than 5 octets or above `2^31-1` (§5.1); Huffman with EOS, padding longer than 7 bits or not all ones (§5.2); an index out of range | `COMPRESSION_ERROR`, connection closed |
| settings, PING, PRIORITY, empty frames, zero WINDOW_UPDATE floods (CVE-2019-9512/9515/9518; "useless" frames) | a budget of control frames that do no work per window; queued responses (PING ACKs, SETTINGS ACKs) capped, and past the cap the server stops reading and closes (Go: `maxQueuedControlFrames` 10,000) | 1,000 cheap frames per 10 s beyond 2 per request; at most 1,000 queued control replies |
| empty header frames (CVE-2019-9516), zero-length DATA without END_STREAM (9518) | same budget; a header field with an empty name is a stream error | |
| data dribble and internal buffering (9511, 9517) | the server never buffers more than the windows it has advertised. The receive window of a stream is the handler's reading; WINDOW_UPDATE is sent when the handler has read half of it | stream window 256 KiB; connection window 1 MiB |
| slow read with h2 (zero window) | a stream write that waits on a window parks; no progress for `writeStallMs` → `RST_STREAM(CANCEL)`; a connection with no progress at all → GOAWAY and close | 30 s |
| flow-control overflow | a WINDOW_UPDATE taking a window above `2^31-1` is `FLOW_CONTROL_ERROR` (stream or connection, RFC 9113 §6.9.1); `SETTINGS_INITIAL_WINDOW_SIZE` changes adjust open streams and may make them negative (§6.9.2) | |
| priority abuse (CVE-2019-9513, resource loop) | RFC 7540 priorities are deprecated (RFC 9113 §5.3.2): PRIORITY frames are validated, counted in the budget and otherwise ignored. Scheduling is round robin, with the RFC 9218 `priority` header's urgency as a hint later | |
| stream state violations (DATA on an idle stream, even stream ids from the client, ids going down) | `PROTOCOL_ERROR` or `STREAM_CLOSED` as RFC 9113 §5.1 prescribes | |
| malformed requests | pseudo-headers first, exactly once, only `:method :scheme :path :authority`; lowercase names; no connection-specific fields; `TE` only `trailers`; no CR, LF or NUL, and no leading or trailing whitespace, in values (§8.2.1); `content-length` must equal the DATA received (§8.1.1) → `RST_STREAM(PROTOCOL_ERROR)` | |
| frame sizes | `SETTINGS_MAX_FRAME_SIZE` stays at 16,384; a larger frame is `FRAME_SIZE_ERROR` | |
| preface and SETTINGS | the client preface and its SETTINGS within the head timeout; our SETTINGS must be ACKed within 10 s | |
| server push | never sent | |
| h2c upgrade smuggling (`Upgrade: h2c` forwarded by a proxy that then tunnels, bypassing its access rules; Bishop Fox's h2csmuggler, 2020) | `Upgrade: h2c` is not supported (RFC 9113 §3.1 deprecated it). The request is served as HTTP/1.1 and the header ignored | |
| H2.CL / H2.TE downgrade smuggling (Kettle 2021) | a problem of proxies that translate to HTTP/1. obx is an origin; it still rejects `transfer-encoding` and a mismatched `content-length` in h2 (above), so a future obx proxy would not forward them | |

Does obx help? A great deal for the memory-safety half. Most HTTP/2 CVEs in
C servers were use-after-free, double free or out-of-bounds in stream
bookkeeping (in nghttp2, Apache's mod_http2, Envoy). Ownership rules those
out, and the stream table is just an array of owned records. The
resource-exhaustion half (the table above) is policy and must be written and
fuzzed like any other server's.

### 3.6 TLS

| Attack or issue | Defence | Default |
|---|---|---|
| **handshake CPU floods** | measured here (`openssl speed`, OpenSSL 3.0.13): ECDSA P-256 sign 16 µs, X25519 31 µs per operation (about 62 µs for a key exchange), RSA-2048 sign 278 µs. A TLS 1.3 full handshake therefore costs about 80–100 µs of the worker's single thread with an ECDSA certificate, against about 350 µs with RSA-2048: roughly 10,000 against 3,000 new connections per second per core. Prefer ECDSA certificates. Cap the handshakes in progress per worker; above the cap, stop accepting (the kernel queues) | `maxHandshakes` 256 per worker |
| slow or stalled handshakes | the handshake counts toward the head timeout | 10 s |
| renegotiation (THC-SSL-DOS, CVE-2011-1473; CVE-2009-3555) | TLS 1.3 has none; for 1.2, `SSL_OP_NO_RENEGOTIATION` | off |
| old versions and ciphers | TLS 1.2 minimum; 1.3 preferred; for h2 over TLS 1.2, only ECDHE with AEAD ciphers (RFC 9113 §9.2.2) | |
| TLS compression (CRIME) | off (OpenSSL's default) | |
| 0-RTT replay (RFC 8446 §8) | early data off; if it is ever enabled, only for safe methods, with `425 Too Early` (RFC 8470) | off |
| ALPN cross-protocol attacks (ALPACA, Brinkmann et al., USENIX Security 2021) | strict ALPN: if the client offers ALPN and neither `h2` nor `http/1.1` is in its list, abort with `no_application_protocol` | strict |
| SNI | the certificate is chosen by SNI (a callback, which runs obx code on the program's thread during `SSL_do_handshake`; allowed for ordinary externs). An unknown name gets the default certificate, or an abort when `Config.strictSni` is set. A request whose Host does not match the connection's SNI gets 421 (RFC 9110 §15.5.20) when strict | default cert |
| session resumption across workers | ticket keys made in the supervisor before the fork, so every worker resumes every other worker's sessions without shared state (concurrency.md, "Without threads"); rotation by rolling restart or by re-reading a key file | |
| memory per TLS connection | OpenSSL's read and write buffers are about 34 KiB while in use. `SSL_MODE_RELEASE_BUFFERS` frees them while a connection is idle (nginx does this) | on |
| library vulnerabilities (Heartbleed, CVE-2014-0160) | the system's OpenSSL, updated by the OS (http-client.md, decision 1) | |

Does obx help? Not with the cryptography, which stays in C, OpenSSL's. The
non-blocking API parks on the socket like libpq (no helper thread).
Ownership frees every `SSL*` exactly once.

### 3.7 Application-level helpers

- **Request ids.** Each request gets 16 random bytes in hex (from
  `getrandom`, read in batches), set in `rq.id` and sent as `X-Request-Id`.
  An incoming `X-Request-Id` or `traceparent` (W3C Trace Context) is used
  only from `trustedProxies`, and only if it is 1–64 token characters.
- **Access logs** (`http.accessLog`): one structured line per request
  (logfmt by default, JSON optional) with the time, request id, worker,
  peer, method, route *pattern*, status, bytes in and out, duration, and
  the outcome (ok, client-gone, timeout, limit, error). Every value derived
  from input is escaped: control bytes, `"`, `\` and invalid UTF-8 as
  `\xHH`, truncated at 256 bytes (log injection, CWE-117). The query
  string, `Authorization` and `Cookie` are not logged by default.
- **Log writes must not stall a worker.** `println` is a blocking write
  on the program's thread. If stdout is a pipe the reader has stopped
  reading, the whole worker stops. The logger appends to an in-memory
  buffer and flushes it every 100 ms or 64 KiB through a `blocking` extern
  `write`. Past 4 MiB unflushed it drops lines and counts the drops.
- **Metrics**: per-worker counters (requests by status class, the rejects
  of each limit, resets, timeouts) through `serve`'s `Stats` and a
  `/metrics` handler the program may route.

## 4. HTTP/2 in obx

### 4.1 Build or bind

| | Bind nghttp2 1.59 (`libnghttp2.so.14`, installed as curl's dependency; no headers) | Write in obx |
|---|---|---|
| Size | nghttp2's `lib/` is 23,751 lines of C plus 10,933 of headers (counted here); the binding is perhaps 1,000–1,500 lines: frame structs read by offset, `nghttp2_nv` arrays in C memory (`ffi.Mem`), and about 15 of the 26 callback types | about 4,000 lines. h2o's server side is 4,445 lines of C (`connection.c` 2,063, `hpack.c` 1,192, `scheduler.c` 434, `stream.c` 438, `frame.c` 310) plus a generated Huffman table (counted here). Go's server, framer and write schedulers in `h2_bundle.go` are about 7,000 lines with comments, plus 1,585 for `hpack`. Rust's `h2` crate is 22,529 lines with client, docs and tests |
| Fit with tasks | nghttp2 runs everything through callbacks during `nghttp2_session_mem_recv`. obx state reaches the callbacks only as a `ptr` through `intoPtr`/`fromPtr`, unchecked by the compiler. Streaming responses need `NGHTTP2_ERR_DEFERRED` and `resume_data`. The tasks, parking flow control and limits are still written in obx around it | direct: a stream is a task; window waits are promises; the limits of section 3.5 are code in the same module |
| Safety | C in the process. nghttp2 is hardened and fuzzed, but it has had CVEs of its own (CVE-2023-44487, CVE-2024-28182, CVE-2023-35945) | memory-safe; logic bugs are possible, and a panic stops a worker (section 2.7) |
| Updates | with the OS | ours |
| Panic risk | a panic in an obx callback ends the program without returning to C (SPEC 12): same as obx code | same |
| Testing | conformance comes mostly from nghttp2 | h2spec, differential tests against nghttp2 (curl), Node's http2 (nghttp2 1.65), Go and hyper, HPACK against Go's `hpack` |

**Recommendation: write it in obx.** The binding is not much smaller than
the code that matters. It would leave the hardest part of the server (stream
state shared with tasks) as unchecked pointer handling, and the library is
meant to show that obx can do exactly this safely. nghttp2's main value, a
reference implementation, is had through testing against it. If HTTP/2 is
needed before S3 is done, put a proxy in front (nginx, h2o, Caddy) that
terminates TLS and HTTP/2 and speaks HTTP/1.1 to obx.

### 4.2 Structure

- **The connection task C** owns the read half and the connection state.
  It reads frames, decodes HPACK, enforces the limits of section 3.5,
  starts a stream task per request on its scope, and delivers DATA to the
  stream inboxes.
- **The writer task W** owns the write half. It drains the connection's
  output queue in frame order and parks on a promise when the queue is
  empty.
- **Stream tasks H** run the handler with a `Request` (inbox) and a
  `Response` (outbox). `w.write` encodes nothing itself. Inside one `with`
  on the connection's `Shared[H2State]` it takes what the stream and
  connection windows allow, appends DATA frames to the output queue,
  completes W's promise and returns. If no window is left, it registers a
  promise and parks on it outside the `with`. Response heads are
  HPACK-encoded inside that same `with`, so the encoder's state follows the
  order in which frames are queued, which is the order they are written.
- `H2State`: the stream table (an array of owned records, indexed by a small
  map from stream id), windows, settings, the HPACK encoder and decoder,
  the output queue, budgets and counters. Nothing in it is borrowed across
  a park.
- A `RST_STREAM` from the client drops H's future, which cancels it. The
  end of the connection ends C's scope, which cancels every H. GOAWAY drains
  them (section 2.7).
- **Memory for an idle h2 connection:** two tasks (C and W, about 16 KB of
  stack), a 16 KiB frame read buffer released while idle, two 4 KiB HPACK
  tables, and the state. The target is at most 40 KB. A busy stream adds
  its task's stack pages (8–16 KB) and its buffered DATA, which the windows
  bound.

### 4.3 Pieces and sizes (estimates)

| Piece | Lines |
|---|---:|
| frames: read and write the 10 frame types, validation | 600 |
| HPACK: integers, strings, static table (61 entries), dynamic table, Huffman decode (a state table generated from RFC 7541 Appendix B as a `const` array) and encode | 900 |
| connection state machine, stream states, SETTINGS, PING, GOAWAY, flow control | 1,500 |
| the bridge to `Request`/`Response`: inbox, outbox, parking writes, trailers, 100-continue | 600 |
| budgets and limits of section 3.5 | 400 |
| total | about 4,000 |

### 4.4 Negotiation

- **TLS + ALPN** (S2): the server's ALPN callback picks `h2` when offered
  (with TLS 1.3, or 1.2 with an allowed cipher), otherwise `http/1.1`.
  ALPN `h2` without HTTP/2 enabled falls back to `http/1.1`.
- **h2c with prior knowledge** (RFC 9113 §3.3), `Config.h2c = true`:
  the HTTP/1 reader sees the 24-byte preface `PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n`
  as the first bytes of a connection and hands the connection to the HTTP/2
  code. Off by default, because on an exposed port it serves only clients
  that know in advance. It is meant for a trusted proxy or load balancer in
  front, and for h2spec in tests (S3 can be gated before TLS exists).
- **`Upgrade: h2c`: not supported** (section 3.5).
- **HTTP/3 (RFC 9114, QUIC RFC 9000, QPACK RFC 9204): not planned.**
  - It is a UDP transport with its own loss recovery and congestion
    control. ngtcp2 and quiche are tens of thousands of lines.
  - OpenSSL 3.0.13 has no QUIC TLS API (the server side arrived in OpenSSL
    3.5).
  - With one thread per worker it would also need UDP load spreading
    (`SO_REUSEPORT` with connection-id steering).

  Advertise it from a proxy with `Alt-Svc` if ever needed.

## 5. Conformance and hardening tests

### 5.1 What is on this machine (checked 2026-11-03)

| Tool | State here |
|---|---|
| curl 8.5.0 (OpenSSL 3.0.13, nghttp2 1.59.0; `--http2`, `--http2-prior-knowledge`) | installed |
| openssl 3.0.13 (`s_client`, `s_server`, `speed`) | installed |
| Go 1.26.4 (`~/go`): `net/http` serves and requests h2c through `Protocols`; `hpack` is vendored in GOROOT; `go test -fuzz` | installed |
| Rust (`~/.cargo`): hyper 1.12 / h2 0.4.20 / httparse 1.10 built here in 17 s from crates.io | installed; the network works |
| Node 24.2 with `http2` (nghttp2 1.65.0) and llhttp 9.3.0 | installed |
| `libnghttp2.so.14` | installed (curl's), no headers, no `nghttp`/`h2load` |
| **h2spec** | not packaged; built here in seconds with `go install github.com/summerwind/h2spec/cmd/h2spec@latest` (into a scratch `GOBIN`) and run: 145 tests. Go's server passes 140 and hyper's 140 (each fails 5 different cases, with a 2 s timeout) |
| h2load, nghttp (`nghttp2-client`), wrk 4.1.0, slowhttptest 1.9.0 | not installed; apt has them (Ubuntu 24.04), which needs sudo |
| ab, hey, oha | not installed (`go install` / `cargo install` would work) |
| http-garden | needs Docker, which is not installed. Its method can be reproduced locally (5.2) |
| Python `h2`/`hpack` | not installed (pip would work) |

### 5.2 The test plan

1. **Unit and scripted tests** (`lib/tests/run/http_server_*.obx`): a
   client task in the same program sends raw bytes and checks the exact
   reply bytes and that the connection is closed. One test per row of the
   tables in sections 3.1–3.4. The models are Go's `net/http` server tests
   (`serve_test.go`: `TestRequestSmuggling`, `TestServerValidatesHostHeader`,
   `TestHeaderToWire`, the timeout tests), hyper's `tests/server.rs`, and
   httparse's tests (strict and lenient cases are listed separately there).
   Port the cases, not the code.
2. **A differential parser fuzzer**, `tests/httpgen.py`, in the style of
   `vrgen.py`. It generates request streams from a grammar with mutations:
   CL/TE combinations, whitespace and case variants, obs-fold, bare CR and
   LF, NUL, chunk sizes, extensions and trailers, pipelined sequences,
   absolute-form, Expect, very long or repeated fields. Each stream goes to:
   - **obx**: a test server that prints, per request, the method, target,
     headers, body bytes and the reject status;
   - **oracles**: Go `net/http` (a 30-line server, as in `http_gate.py`),
     Node (llhttp), hyper (httparse) and curl/nghttp2 where relevant.

   The properties checked:
   - obx accepts only streams that every oracle accepts and frames the same
     way (same request boundaries, same bodies);
   - where the oracles disagree, obx rejects;
   - obx never exits with anything but 0, and leaks nothing under
     `OBX_CHECK=1`.

   This is the HTTP Garden's method (differential fuzzing of request
   streams) without Docker.
3. **HPACK differential**: a Go harness with the vendored `hpack` encodes
   random header lists, with random table sizes, Huffman choices and size
   updates. obx decodes them, and the reverse direction is checked too.
   Malformed blocks (bad integers, Huffman padding, EOS, indexes out of
   range) must give `COMPRESSION_ERROR`, never a panic.
4. **h2spec** against obx's h2c (S3 gate) and later over TLS (`-t -k`).
   Passing everything is the target; the mature servers above do not, so
   the gate is "every failure is understood and listed".
5. **Hardening scenarios** as obx programs (tasks make attackers cheap):
   - slowloris: 10,000 connections sending a byte every 5 s;
   - slow POST and slow read, with a zero h2 window;
   - Rapid Reset, a CONTINUATION flood, an HPACK bomb, PING, SETTINGS and
     empty-frame floods, and MadeYouReset, each as a raw-frame script;
   - an accept flood;
   - `EMFILE` reached with `ulimit -n`.

   Each scenario passes when the server answers a legitimate client within
   its p99 budget throughout and its memory stays under `memBudget`.
   `slowhttptest`, `h2load` and smuggler.py (defparam) or PortSwigger's
   HTTP Request Smuggler are useful extras when installed.
6. **Load**: `cmd/httpload.obx` for HTTP/1.1, as today. For HTTP/2 a Go h2c
   load client (40 lines; used for section 6) until `h2load` is installed,
   and later an h2 mode in httpload itself.
7. **Sanitizers and both backends**: every test on the C and native
   backends, under `OBX_CHECK=1`, ASan and UBSan, as for the rest of the
   library.

## 6. Performance targets

Measured here (2026-11-03): each server pinned to CPU 0, the native
`cmd/httpload.obx` on CPU 1, 4 s per row, 2,000 idle keep-alive connections
held open for the RSS figure, `GET /` returning `hello, world\n`. The servers
are `cmd/httpd.obx` (native), Go 1.26.4 `net/http` (GOMAXPROCS=1, h1 and
h2c), and hyper 1.12 on tokio's `current_thread` runtime (h1 and h2c).

| HTTP/1.1, one core | obx httpd | Go net/http | hyper |
|---|---:|---:|---:|
| RSS per idle connection | 12.7 KB | 20.6 KB | 15.9 KB |
| 1 client, req/s | 4,170 | 4,250 | 3,870 |
| 10 clients, req/s | 37,700 | 31,800 | 34,400 |
| 100 clients, req/s | 68,000 | 43,400 | 61,800 |
| 100 clients, p50 / p99 | 1.47 / 2.22 ms | 2.18 / 6.15 ms | 1.60 / 2.99 ms |

| HTTP/2 (h2c), server on one core; Go h2c load client on 4 cores | Go | hyper |
|---|---:|---:|
| 1 connection, 1 stream at a time | 2,760 | 2,970 |
| 1 connection, 10 streams | 18,500 | 25,200 |
| 1 connection, 100 streams | 35,000 | 87,700 |
| 10 connections × 10 streams | 29,400 | 89,600 |

These match PERFORMANCE.md's C1b table (obx 69,400 at 100 clients, Go
46,200). The HTTP/2 client may be the limit for hyper's rows. With 100
connections, the single-threaded `httpload` may be as much of a limit as
the server.

Targets for the library (on this machine, one core):

| | Target | Why |
|---|---|---|
| h1 `GET /` through the router, middleware off | ≥ 60,000 req/s at 100 clients (today's httpd minus at most 15 %) | header map, routing, date and request id must cost under about 2 µs together |
| h1 with request ids and the access log on | ≥ 50,000 req/s | |
| h1 idle connection | ≤ 16 KB RSS | today 12.7 KB |
| h2c `GET /`, 100 streams on one connection | ≥ 60,000 req/s | between Go and hyper; one task start (about 35 ns) and a few `with`s per stream are small next to HPACK and the system calls |
| h2 idle connection | ≤ 40 KB RSS | two tasks, two HPACK tables |
| TLS new connections (ECDSA P-256, TLS 1.3) | ≥ 8,000 handshakes/s per worker | about 80–100 µs of CPU each (section 3.6) |
| with workers | C2's scaling: 134,900 req/s at 4 workers (PERFORMANCE.md); the library should keep that within 15 % | |
| Postgres route | the C1c figures (about 23,000 req/s per core at 10–100 clients, bounded by the 40 µs of system calls per request) | |

## 7. Phases

Sizes are library lines without tests; tests are usually as many again.

**S1. The HTTP/1.1 server core** (about 2,600 lines; `lib/http.obx` family:
`httpserver.obx`, or `http` grown, as decided with H1's module layout).

- The parser shared with the client, with the server's strict mode (section
  3.1).
- The connection loop: keep-alive, pipelining in order, yields.
- `Request` and `Response`, with `Content-Length`/chunked bodies both ways,
  flush, trailers, 100-continue, the full-duplex default, HEAD, 204 and 304.
- The router (Go 1.22 patterns), the middleware chain, `Error` and `check`.
- Every limit and timeout of section 3.4, the per-route deadline, and
  cancellation on hangup.
- Request ids, the access log, the security headers, graceful drain, and
  `serve` in workers.
- In `net`: `Conn.split()`, the hangup registration, `TCP_DEFER_ACCEPT`,
  and the spare descriptor.
- In `task`: `workerReady()` and `onCancelCall` (the latter could move to
  S4 if needed).
- `cmd/httpd.obx` rewritten on the library as the example.

Gate:

- all scripted tests and the differential fuzzer (100,000 streams, no
  difference, no exit other than 0) pass;
- the hardening scenarios for slowloris, slow POST, slow read, the accept
  flood and `EMFILE` pass;
- performance meets the h1 targets of section 6;
- no leaks under `OBX_CHECK=1`, ASan or UBSan on both backends.

**S2. TLS and ALPN** (about 500 lines over H2's binding):

- `SSL_accept` on the parked socket, certificates and keys, SNI selection;
- ALPN selection, still `http/1.1` only until S3;
- the TLS rows of section 3.6, and ticket keys made before the fork.

Gate: `curl` and `openssl s_client` over TLS 1.2 and 1.3, with the failure
cases (an old version, unknown SNI with `strictSni`, ALPN mismatch) giving
the right alerts; the handshake rate target; no leaks.

**S3. HTTP/2** (about 4,000 lines; section 4):

- framing, HPACK, streams as tasks, flow control and the budgets of
  section 3.5;
- h2c with prior knowledge, then ALPN `h2` on S2's TLS.

S3's h2c part does not need S2 and can run in parallel with it.

Gate:

- h2spec with every failure listed and explained;
- the HPACK differential test;
- every HTTP/2 hardening scenario of section 5.2;
- the same handler test suite run over h1 and h2 with identical results;
- the h2 performance targets.

**S4. Extras, as programs need them.** Each is small (200–600 lines):

- an SSE hub helper;
- static files (`openat2`, ranges, `ETag`/`If-None-Match`, a precompressed
  `.gz`);
- response compression (gzip through zlib, opt-in, never on responses that
  carry secrets alongside input, because of BREACH);
- `Content-Encoding` request bodies with a ratio limit;
- WebSockets (RFC 6455, and RFC 8441 for h2);
- form and multipart parsing with limits;
- per-IP limits across workers through a counter area shared before the
  fork;
- the black box (section 2.7);
- `/metrics`.

**Composition with workers and databases.**

- One `serve` per worker on the shared listener (C2's default).
- Each worker owns its database pools. The budget is workers × pool size ≤
  the database's limit minus a reserve. For Postgres that is
  `max_connections` (100 by default) minus `superuser_reserved_connections`;
  past that, use pgbouncer. `http.serve` logs the product at start.
- SQLite: a `Db` per task or per worker in WAL mode, with
  `sqlite3_busy_timeout` (it runs inside the blocking step, on a helper).
  Writes are serialised by SQLite's file lock across every worker.
- SQL Server: pool size ≤ `OBX_HELPERS` − 2 per worker (section 2.5).

## 8. Language and runtime questions for the user

The library works without any of these. Each is a question with a
recommendation.

**Decided 2026-11-03: every recommendation below is accepted.**
- No interface values or closures for handlers (1).
- Error conversion in `try`: later (2).
- Derived decoding: later, designed with `wire` (3).
- "Can park" checking: later, as decided (4).
- No "cannot panic" check (5).
- `task.Frozen[T]`: yes (6).
- Seeded string hashing: yes, as SipHash-1-3 with a per-process key (7).
- `task.onCancelCall`: yes, in S1 (8).
- No panic per task (9).
- Shared integer counters: S4 (10).

1. **Interface values or escaping closures for handlers?** Today a handler
   is a plain `fn` value with an `app: &A` parameter. Go's `Handler`
   interface and closures over dependencies would read better.
   Recommendation: not for this library. Plain functions plus a copy state
   type cover it with no allocation and no hidden capture. Revisit when
   interface values land (SPEC 13); then `Handler` could also be an
   interface.
2. **Error conversion in `try`.** `try` needs the same error type, so every
   database call in a handler goes through `http.check(r, status)`.
   Recommendation: yes, later. `try` converts `E` to `F` when `F` has an
   associated `from(e: E): F` (Rust's `From`), with the conversion visible
   in the declarations.
3. **Derived decoding of bodies into records** (JSON, forms), like serde or
   Go's `encoding/json`. Recommendation: design it together with the `wire`
   constraint of process futures (concurrency.md section 9), which needs
   the same per-type generated code. Until then, a `json` reader and
   decoders written by hand, with depth and size limits.
4. **Compile-time "can park" checking** (Q9 of concurrency.md, already
   decided as later). The server benefits from it: a handler that parks
   inside `with` is a trap at run time today. No new question.
5. **A "cannot panic" check** for parsing modules (an effect, SPEC 13).
   Recommendation: no language change for now. Use the lint, fuzzing, and
   checked arithmetic on input (section 2.7). Revisit if effect summaries
   arrive, since "may panic" is one more effect.
6. **A read-only shared handle** (`task.Frozen[T]`: a counted copy handle
   whose `get()` returns `&T` and may be held across parks, because nothing
   can change the value). It would hold the router, configuration and
   templates without a copy per lookup and without `with`. Recommendation:
   yes; it is small, and sound for the same reason `once` globals are.
7. **Seeded string hashing in std** (section 3.2). Recommendation: yes,
   a per-process random seed (or SipHash-1-3) for `str.hash`, so that a
   `Map` keyed by input is not open to hash flooding. A std change, not a
   language change.
8. **Cancelling a blocking C call** (`task.onCancelCall`, section 2.5).
   Recommendation: a library and runtime call, no syntax; the C function it
   names must be documented as callable concurrently (`sqlite3_interrupt`,
   `SQLCancel`, `PQcancel`).
9. **Panic per task** (unwinding) so one bad request does not stop a
   worker. Recommendation: no (concurrency.md Q5 stands). Use the
   ready-aware restart policy and the black box instead (section 2.7).
10. **Shared counters across workers** (an `mmap`ed area with atomic
    increments in C, made before the fork) for global rate and connection
    limits. This is a narrow exception to shared nothing, nginx's zones.
    Recommendation: allow it as a library facility in S4, holding integers
    only, never obx values.
