Part of the obo language reference. Index and core syntax: [LANGUAGE.md](../LANGUAGE.md).

## 9. Runtime checks and exit codes

Decimal exact arithmetic traps with exit 10 and diagnostic ids 40-49: 40 is
"not exactly representable", 41 overflow, 42 division by zero, 43 invalid, 44
unsupported range, 45 precision overflow, 46 exponent range, 47 display range
and 48 buffer too small; 49 is other. The ids are trap diagnostics only.

`decimal::div`, `decimal::quantize` and `decimal::toInt` do not trap for value
errors; their `Result` `err` code is the reviewed core status:

| Result code | Core status | Meaning |
|---:|---|---|
| 1 | `DEC_OVERFLOW` | overflow / not representable in the 38-digit range |
| 3 | `DEC_INEXACT` | `UNNECESSARY` would discard a nonzero digit, or `format` would drop a nonzero digit |
| 4 | `DEC_INVALID` | invalid value, invalid text or rounding mode |
| 5 | `DEC_DIV_ZERO` | zero divisor |
| 6 | `DEC_UNSUPPORTED_RANGE` | request outside the API range (scale not 0..32768, or `check` metadata) |
| 7 | `DEC_PRECISION_OVERFLOW` | too many significant digits for `parse`/`check` |
| 8 | `DEC_EXPONENT_RANGE` | `parse` exponent outside `int16` |
| 9 | `DEC_DISPLAY_RANGE` | `parse`/formatter text or display-scale bound |
| 10 | `DEC_BUFFER_SMALL` | a text or `Result` cell could not be allocated |

Core status 2 (`UNSUPPORTED_EXACT`) stays reserved. For the text APIs,
`parse` produces 4/7/8/9/10, `format` 3/9/10, `canonical` 4/9/10 and `check`
3/6/7/10; defensive 1/4/6 remaps are described in the design. Scale and mode
are validated before the core is called: constant invalid values are compile
errors, runtime values return 6 (scale) or 4 (mode). `decimal::check` metadata
is data, so an out-of-range constant `p`/`s` returns 6 at run time.

| Exit code | Cause |
|---|---|
| 2 | negative array length, or out of memory |
| 3 | index out of range (array, string, `substr` or `truncate`), with the line |
| 4 | an internal error, such as a misaligned stack in a runtime call |
| 5 | with `OBO_CHECK=1`: the program ended with live objects (a leak) |
| 6 | with `OBO_CHECK=1`: a double free |
| 7 | a handle was used after its object was freed |
| 8 | with `OBO_CHECK=1`: the program ended with file descriptors opened by `openFile` and never closed |
| 9 | with `OBO_CHECK=1`: a tracked file descriptor was closed twice or used after it was closed |

Division by zero stops the program with a hardware trap (SIGFPE).

The `code` of an I/O `Result` is the OS errno, positive (0 means unspecified);
the trap codes 2 to 9 are a different namespace and overlap numerically with
errnos (ENOENT is 2, EBADF is 9), so a program must not do `exit(r.code)`:
a missing input file would look like an out-of-memory trap. Print the message
and choose the exit code deliberately.

Environment variables:

- `OBO_CHECK=1` counts allocations and frees, and makes leaks and double frees
  exit with codes 5 and 6.
- `OBX_GC=poison` overwrites freed memory, so a use of freed memory shows up as
  garbage rather than as a value that happens to still be there.
- `OBO_STATS=1` prints the exact live peak and total allocation at exit.
- `OBO_HEAP_LIMIT` sets the runtime heap arena. The compiled default is 4 GiB
  and the compiled ceiling is 64 GiB: 2^32 granules of 16 bytes, the full low
  32-bit handle offset, with the final granule at 0xffffffff. The value is bytes
  or bytes with an exact `KiB`, `MiB` or `GiB` suffix, a multiple of 16, at
  least 64 KiB and at most the ceiling. Invalid or above-ceiling values print
  `obo: invalid OBO_HEAP_LIMIT: <value>` and exit 2 before mapping; there is no
  silent clamp. The limit controls the arena, not any single object: one
  allocation's payload remains at most 4 GiB - 32 bytes. `MAP_NORESERVE` means
  the arena is address space, not committed RAM; `RLIMIT_AS`, cgroups and host
  overcommit still apply.
