# H1a0 limits, status mapping and exact boundaries

Status: experimental and unstable. These are starting points from the approved
HTTP/1.1 design, not protocol constants or a security guarantee by themselves.
`http1core::validateLimits` revalidates every construction; `makeParser` and
`makeResponse` reject an invalid set.

## Defaults (`http1core::defaultLimits`)

| Field | Default | Meaning |
|---|---:|---|
| `maxStartLine` | 8192 | request line including CRLF |
| `maxTarget` | 4096 | raw request-target bytes |
| `maxHeaderBytes` | 16384 | start line + field lines + final CRLF |
| `maxHeaderCount` | 100 | field lines, excluding the blank line |
| `maxHeaderLine` | 8192 | one field line including CRLF |
| `maxBody` | 8388608 | decoded request-body bytes |
| `maxChunkLine` | 1024 | chunk-size line with extensions and CRLF |
| `maxChunkSize` | 1048576 | one decoded chunk |
| `maxChunkCount` | 65536 | data chunks (the last zero chunk is not counted) |
| `maxChunkMetadata` | 65536 | cumulative chunk-size/extension lines, including the final 0 line |
| `maxTrailerBytes` | 4096 | request trailer field lines |
| `maxTrailerCount` | 32 | request trailer fields |
| `maxResponseHeaderBytes` | 16384 | response status line + fields + CRLFs |
| `maxResponseBuffer` | 32768 | encoded pending response bytes |
| `maxResponseTrailerCount` | 32 | response trailers |
| `maxResponseTrailerBytes` | 4096 | response trailer name/value/CRLF budget |
| `maxPipelineBytes` | 24576 | buffered bytes beyond the current message |
| `maxRequests` | 1000 | requests per parser/connection before close |
| `allowHttp10` | false | opt-in HTTP/1.0 compatibility |
| `allowExtensionMethods` | false | unknown token methods are `501` when false |
| `pipelining` | true | sequential bounded pipelining |
| `autoContinue` | true | policy input for the connection's 100-continue path |
| `serverName` | `""` | response `Server` product token; empty omits it |

The design's connection/timeout/network fields (`maxConnections`,
`maxTotalConnectionBytes`, accept/header/body/idle/response deadlines) are not
part of H1a0 because it performs no I/O.

## Validation

`validateLimits` requires:

- every numeric limit positive;
- `maxTarget + 44 <= maxStartLine`;
- `maxStartLine <= maxHeaderBytes`;
- `maxHeaderLine <= maxHeaderBytes` and
  `maxHeaderBytes + maxHeaderLine <= 16 MiB`;
- `maxChunkLine <= maxChunkMetadata`, `maxChunkLine <= maxHeaderBytes +
  maxHeaderLine`, and `maxChunkSize <= maxBody`;
- `maxResponseHeaderBytes <= maxResponseBuffer`;
- `maxResponseBuffer >= 256` (a minimal header block and final chunk/trailer
  block fit) and `maxResponseBuffer <= 16 MiB`;
- `maxResponseTrailerBytes + 16 <= maxResponseBuffer`;
- `maxPipelineBytes <= maxHeaderBytes + maxHeaderLine`;
- `serverName` is at most 64 bytes and, when non-empty, a valid token.

Overflow and budget violations are `err(..., code)` before any buffer is made.
The parser input buffer is exactly `maxHeaderBytes + maxHeaderLine` bytes and
never grows. `feed` is all-or-nothing: if one feed cannot fit, that call copies
nothing and the parser enters a terminal error (`414` while reading a start
line, `431` while reading headers, `400` otherwise). `feed` also returns `false`
after a clean EOF or after `Closed`; the parser cannot be resurrected. Callers
should feed bounded pieces (for example transport read sizes) rather than a
whole large body.

## Exact boundary semantics

- Start-line and field-line limits include their CRLF. The terminating blank
  line is two bytes and counts toward `maxHeaderBytes`.
- `maxHeaderCount` counts field lines only.
- `maxTarget` counts the raw target bytes only.
- A fixed body of exactly `maxBody` is accepted; a larger `Content-Length` is
  `413` before any body read.
- A chunk of exactly `maxChunkSize` is accepted; larger is `413`. Decoded bytes
  over `maxBody` are `413`.
- `maxChunkCount` counts data chunks. Exceeding it is `400` (framing abuse).
- `maxChunkMetadata` sums every chunk-size line, including extensions and CRLF
  and the final `0` chunk-size line. Chunk-data CRLFs are not part of it.
- Request trailer count/bytes use their own limits; the final blank line is not
  counted in `maxTrailerBytes`.
- `maxPipelineBytes` is checked when a completed message is followed by bytes
  that belong to the next message. Exceeding it is `400`.
- `maxRequests` is per parser/connection. The last allowed request has
  `keepAlive == false`; after its body the parser returns `Closed` and never
  parses another buffered request. There is no protocol-generated `429` in
  H1a0; `Closed` is the terminal result. `429` remains an application/status
  table entry only.
- `pipelining == false` also has `keepAlive == false`. Already-buffered bytes
  for a second request are `400`; a later sequential feed is refused because
  `Closed` is terminal.
- Start-line and target limits are distinct: `maxStartLine` is checked before
  parsing, and a target over `maxTarget` is `414`. Because
  `maxTarget + 44 <= maxStartLine`, a line over `maxStartLine` is usually also
  over `maxTarget`; both map to `414`.
- Host/authority IPv6 support validates bracketed hex/colon/dot syntax and the
  port; it is not a full IPv6 address-semantics validator.

## Request status mapping

| Condition | Status |
|---|---:|
| malformed start line, field, chunk, trailer, target | `400` |
| start line or target over its limit | `414` |
| header line/block/count over limit | `431` |
| declared or decoded body over `maxBody` | `413` |
| chunk size over `maxChunkSize` | `413` |
| unsupported transfer coding | `501` |
| unsupported/disabled or CONNECT/TRACE method | `501` |
| Upgrade / `Connection: upgrade` | `501` |
| unsupported HTTP version, HTTP/1.0 when disabled | `505` |
| unsupported expectation / multiple Expect | `417` |
| `maxRequests` exhausted | no response, `Closed` |
| connection ended before a declared boundary | `400` |
| idle EOF before any request | no response, `Closed` |

`Error` is returned once with the status, then `Failed` for every later `next`.
An error never resumes parsing. `400` means ambiguity or malformed syntax;
unsupported but well-formed features use `501`/`505`/`417`/`413` as above.

## Response limits and framing

- The output buffer is `maxResponseBuffer`. `writeBody` accepts a prefix that
  fits and returns that count; `0` means drain with `takeOutput` and retry the
  same remaining bytes. `endResponse` returns `false` while the final
  chunk/trailer block does not fit.
- `maxResponseHeaderBytes` is enforced when headers commit. Exceeding it is an
  application/writer error; no partial header block is written.
- `maxResponseTrailerCount` and `maxResponseTrailerBytes` bound trailers;
  trailers are valid only for a chunked response.
- Fixed framing requires exactly the declared body length for a normal response.
  HEAD and 304 use the length as metadata and never send body bytes.
- `204` and `1xx` never send `Content-Length`; `205` sends only
  `Content-Length: 0`; `304` may send length metadata; `HEAD` sends length only
  when the application set one.
- HTTP/1.0 never uses chunked framing. With a known length it sends
  `Content-Length`; without one it is close-delimited and always sends
  `Connection: close`.
