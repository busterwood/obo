Part of the obo language reference. Index and core syntax: [LANGUAGE.md](../LANGUAGE.md).

## 11. Differences from obx

- There is no garbage collector. Memory is freed at scope exit, on reassignment,
  or by `drop`, and the compiler checks the freeing.
- References (`&T`) borrow owned values. They are checked by the
  compiler rather than by runtime collection.
- Handles (`^T`) are the way to write back-references and graphs. They are checked
  at run time.
- New built-ins: `take`, `drop`, `replace`, `handle`, `alive`, `ok`, `err`,
  `openFile`, `readFd`, `writeFd`, `closeFile`, `readDirFd`, `statPath`,
  `lstatPath`, `readLinkPath`, `isatty`, `getenv`, `terminalWidth`, `sleepMs`,
  `stdin`, `stdout`, `stderr`, and
  the prefix operator `try`.
- `Result[T]` is a built-in parameterized type, inspected with `match` (including the
  consuming `ok(v)`/`err(m, c)` form).
- Modules are source imports: `module Name`, `import "Name.obx" [as alias]`,
  `pub`, and `alias::name`. obx's `module`/`import`/`pub` tokens were accepted
  but unimplemented; obo has no unqualified or selective imports and no
  re-exports in Stage 1.
- There is no collector and no `gc()`/`gcstats()` builtin.
- Strings are reference counted rather than collected.
