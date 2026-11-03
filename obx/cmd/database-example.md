# Example: an HTTP server that queries PostgreSQL

`cmd/httpd.obx` is a small HTTP/1.1 server on one thread with one task per
connection (or several worker processes: [Several cores](#several-cores)). Given a pool size, it answers `GET /db` with a query through a
pool of PostgreSQL connections. The whole server is under 300 lines, and
the database part is about 20 of them. The rest is in
[`lib/postgres.obx`](../lib/postgres.obx) (the pool and the non-blocking
queries) and the task runtime (`lib/task.obx`, `lib/net.obx`; see
[../design/concurrency.md](../design/concurrency.md)).

## Running it

From `obx/`, with PostgreSQL listening on its local socket:

```sh
compiler/out/obxc cmd/httpd.obx -o httpd          # add --native for the native backend
./httpd 127.0.0.1:8080 16                         # ADDR, then the pool size
```

```console
$ curl localhost:8080/db
db 2
$ curl localhost:8080/db
db 3
$ curl localhost:8080/shutdown
bye
```

and the server prints:

```
listening on 127.0.0.1:8080
stopped: accepted 3, requests 3, open 0, pool open 1 idle 1 waiting 0
```

The pool connects to `$OBX_PG_DSN`, a libpq connection string. The
default is `host=/var/run/postgresql dbname=postgres`. Without a pool size
`/db` answers 503.

## The database code

**Startup.** The second argument is the pool size. No connection is opened
until a request needs one.

```obx
let db: Option[postgres.Pool] =
    if size > 0 { Some(postgres.newPool(os.envOr("OBX_PG_DSN", "host=/var/run/postgresql dbname=postgres"), size)) } else { None }
```

**The route.**

```obx
if rq.path == "/db" {
    match db {
        Some(pool) {
            match query(&pool, n) {
                Ok(v) { return Reply{status: "200 OK", body: "db " + intToStr(v) + "\n", close: false} }
                Err(e) { return Reply{status: "503 Service Unavailable", body: e.toStr() + "\n", close: false} }
            }
        }
        None { return Reply{status: "503 Service Unavailable", body: "no database\n", close: false} }
    }
}
```

**The query.** `n` is the request's number, so the answer is the request
count plus one.

```obx
// One query on a connection of the pool, given back when the lease drops.
fn query(pool: &postgres.Pool, n: int): Result[int, postgres.Error] {
    let c = try pool.acquire()
    let rows = try c.query("SELECT $1::int8 + 1", &[postgres.Value.Int(n)])
    return rows.int(0, 0)
}
```

That is all the database code in the server. It reads like blocking code,
with no callbacks and no `async` or `await`. The pool and the task runtime
make it concurrent.

## What happens on one `GET /db`

**1. A task for the connection.** The accept loop starts one task per
connection:

```obx
s.spawn(serve, Job{c: c, st: sv.st, db: sv.db})
```

`session` loops reading requests. When the socket has no data, `c.read`
parks the task, and the event loop (epoll) wakes it when bytes arrive.
10,000 idle connections are 10,000 parked tasks of about 13 KB each.

**2. `pool.acquire()` gets a connection.** The pool's state is a
`task.Shared[PoolState]`: the open count, the idle connections, and a queue
of waiting tasks. Inside one `with` it does one of three things:

- **An idle connection is there:** take it.
- **Fewer than `size` connections are open:** reserve a place, and connect
  outside the `with`.
- **All `size` are in use:** join the waiter queue with a `Promise`, and
  park on its `Future`. The task sleeps until a connection is handed to it.

`acquire` returns a `Lease`, which owns the connection while the request
uses it.

**3. `c.query(...)` sends the query without blocking.** The library uses
libpq's non-blocking API:

1. `PQsendQueryParams` writes the query and returns at once.
2. While libpq still has bytes to send (`PQflush`), the task parks on the
   socket until it is writable.
3. While `PQisBusy` says the result is incomplete, the task parks on the
   socket until it is readable; then `PQconsumeInput` reads what arrived.
4. `PQgetResult` collects the result.

While this task waits for PostgreSQL, the thread runs other tasks: other
HTTP requests, or other queries on other connections of the pool.

**4. The lease goes back when it drops.** When `query` returns, the `Lease`
goes out of scope and its `drop` runs:

```obx
fn (l: mut &Lease) drop() {
    ...
    if c.idle() {
        handOn(&l.pool, Some(c))     // to the first waiter, else back to the idle list
    } else {
        c.close()                    // broken, or left in a transaction: closed
        handOn(&l.pool, None)        // the next waiter opens a new one in its place
    }
}
```

`handOn` completes the first waiter's promise with the connection, which
wakes that task, or puts the connection on the idle list. Because the
return is tied to `drop`, a connection cannot leak, even on an early `try`
return or an error. A connection in a bad state is closed, not reused.

**5. The reply** is added to the output buffer and written. The task goes
back to waiting for the next request on the kept-alive connection.

## What the code does not show

**`w: Where`.** The library's `acquire` and `query` take a last `w: Where`
parameter, and the compiler supplies the call site, like Rust's
`#[track_caller]`. If a call that can park is made inside `Shared.with`,
where parking is not allowed, the trap names the line in `httpd.obx`, not
one inside the library.

**Deadlines and cancellation.** Wrapping the call in `task.within` gives it
a budget in milliseconds:

```obx
match task.within(50, fn(): Result[int, postgres.Error] { return query(&pool, n) }) {
```

If the deadline passes while waiting for PostgreSQL, the library sends
`PQcancel` to the server, reads the cancelled query's results so the
connection is idle again, and returns `E_TIMEOUT`. A task cancelled
meanwhile gets `E_CANCELLED`. A task still waiting in the pool's queue
just leaves it.

**Helper threads are not used for queries.** Queries go through epoll on
the connection's socket. Only the parts of libpq that cannot avoid blocking
run on the helper pool (the `blocking` externs of SPEC section 12): name
lookup and TLS while connecting, and `PQcancel`. SQLite's steps and SQL
Server's calls, which have no non-blocking API, use the helper pool for
everything.

**Shutdown.** `GET /shutdown` completes a promise that `main` waits on.
`main`'s scope then ends, which cancels the accept loop and every
connection's task. Each parked read returns "cancelled", the leases drop,
the connections go back to the pool, and the program prints its counters
(`pool open N idle N waiting 0`). It ends with no leaks under `OBX_CHECK=1`.

## How fast

The gate of concurrency stage C1c (`cmd/http_gate.py --db 4,16`): the
server pinned to one core, PostgreSQL 16 over its Unix socket, and 10,000
idle connections held open throughout.

| clients | requests/s | median latency |
|---|---:|---:|
| 1 | about 2,800 | 0.34 ms |
| 10 | about 22,000 | 0.44 ms |
| 100 | 23,000 to 27,000 | 3.6 to 4.3 ms |

From about 10 clients on, the limit is the server's one thread, at about
40 µs per request, mostly system calls. Using more cores means several
worker processes, the next section. Full numbers are in
[../../PERFORMANCE.md](../../PERFORMANCE.md).

## Several cores

A third argument runs the server as that many worker processes:

```sh
./httpd 127.0.0.1:8080 4 8                        # ADDR, the pool size, 8 workers
```

```
listening on 127.0.0.1:8080
8 workers, one shared listener
```

`main` makes the listener, then calls `task.workers`, which forks the
workers; each runs the same `run` function as the one-process server, with
its own event loop, its own counters and its own pool:

```obx
let r = task.workers(n, fn(i: int) {
    task.watchSignals(&[task.SIGTERM, task.SIGINT])   // before anything: a stop is never lost
    let ln = if reuseport { listenOr(at, true) } else { replace(mut &lst, None).unwrap() }
    run(ln, size, i)
})
```

**Each worker has its own pool**, opened in the worker: a database
connection cannot cross a fork (its socket and libpq's state would be
shared by two processes), and `task.workers` refuses to fork while one is
open (exit 15). So the server holds up to **workers × pool size**
connections to PostgreSQL, 8 × 4 = 32 here. That total has to fit the
server's `max_connections` (100 by default, a few of them reserved), with
room for everything else that connects; past that, put pgbouncer in
between. A worker's pool opens its connections as its requests need them.

**The listener is shared.** It is made before the fork, and each worker
adds it to its own event loop at its first `accept`. The kernel keeps one
queue of new connections and wakes one waiting worker for each
(`EPOLLEXCLUSIVE`). A keep-alive connection stays with the worker that
accepted it.

**State is per worker.** The workers share no memory: `/stats` reports the
counters of the worker that answered (its first line says which), and
every worker's request numbers start at 1, so `db N` repeats across them.
Anything the workers must agree on lives in the database.

**Stopping.** `GET /shutdown` reaches one worker, which asks the supervisor
(`task.stopWorkers()`); the supervisor sends `SIGTERM` to every worker. Each
is waiting for it in `task.terminated()` and stops the way the one-process
server does: its accept loop and connections are cancelled, its leases go
back to its pool, and it prints its counters. `SIGTERM` or Ctrl-C to the
server does the same. Then the supervisor prints how each worker ended:

```
stopped: worker 0: accepted 3, requests 3, open 0, pool open 1 idle 1 waiting 0
...
stopped: worker 0 (pid 4711): exit 0, drained
...
```

**A worker that dies** (a panic, a signal) is started again after a short
backoff (100 ms at first). The connections it held are lost, with the one
request in flight on each; the others, and new connections waiting in the
shared queue, are not affected. If workers keep dying (more than 5 times in
10 s) the supervisor stops them all and the server exits with status 1.

How fast, with the load on four other cores (`cmd/http_gate.py --workers
1,2,4,8 --db 4`, 256 connections):

| workers | `/db` requests/s | median latency |
|---:|---:|---:|
| 1 | 22,500 | 11.0 ms |
| 2 | 36,800 | 3.4 ms |
| 4 | 51,100 | 4.3 ms |
| 8 | 71,300 | 3.4 ms |

Native build; the C build is within 5 %. PostgreSQL runs on the same
machine, unpinned, and competes for the cores.
