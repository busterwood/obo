# C interop v2: handoff plan

Status as of 2026-11-01. This note is for whoever continues the work. The design itself is
[`cinterop-boundary-design.md`](cinterop-boundary-design.md) (revision 4, approved). This note covers
where the work stands, how to run it, and when to stop and ask the user.

## Where it stands

- **Design:** approved, with the user's decisions recorded in design §7.1.
- **Stage (a)**, borrowed input and scalar kinds: merged to `main` as `e1e0c47`. The seed was promoted
  in `ea2f335`. Commits on the stage branch:
  - `0d6ff91`, `3845739`, `5277a75`, `48fcd90`: the implementation;
  - `bbc1c41`, `314335c`, `02b6835`, `b2b20e0`: fixes from the first review;
  - `c0640a3`, `1d9cb1e`, `68191f2`: fixes from the second review.

  Design §8 records what stage (a) actually does.
- **Stages (b) and (c)**, copy-out/typed returns and body-less `native fn` with sidecar v2: merged to
  `main` as `85923af` after independent review (verdict APPROVE WITH NITS; nits closed in `472872f`).
  The seed was promoted in `67dbd8c`. The live PostgreSQL checks passed on the branch and again on
  `main` after promotion. Design §8 records what they actually do.
- **Stage (d)**, foreign handles: merged to `main` as `644d920`; the seed was promoted in `6f83880`.
  Design §8 records what it actually does, including the documented deviation (internal
  `__foreignClose`/`__foreignIsOpen` builtins plus wrapper methods instead of compiler-generated
  bodies) and the deferral of `out_own`/`out_offset` to stage (f).
- **Stage (e)**, the libpq v2 port: merged to `main` as `ef11ed7`. Design §8 records the
  implementation, the retained old adapter for `db/postgres`, and the live SELECT-only checks.
- **Stage (f)**, out parameters and the SQLite v2 port: merged to `main` as `49ae601`. Design §8
  records the implementation, the one deviation (an out_offset-only `var` is freed at scope end, so
  the sketched exit 5 is unreachable), and the gate results. The trusted helpers live in
  `obo/experimental/db/sqlite/sq_stmt_errcode.c` and `sq_prepare_at.c` (linear `exec`); the binding
  no longer has a C adapter.
- **Stage (g)**, the `db/postgres` v2 port: merged to `main` as `65492c0`. Design §8 records the
  implementation, the two accepted `open` error changes, the single obo decimal validation, the
  spec-drift guard, and the nested-handle census. The old postgres adapter, libpq token adapter,
  `postgres_adapter_test.c` and `postgres_fake_sql.cbind` are deleted; `cres.c/.h` remain for the
  zlib adapter and the buffer example.
- **Stage (h)**, the skill/template rewrite and the docs status sweep: in progress on
  `cinterop-v2-h`. It teaches the v2 surface (foreign handles, out records, `release_result`, the
  kinds and the census) and moves the templates off `cres`.
- **Cleanup audit:** complete. Its items are folded into stages (e)–(h); nothing else is scheduled
  for deletion, and `cres.c/.h`/`buffer.obx` stay because zlib uses them.

## Decisions that are final

Do not reopen these.

- **Cleanup works like files.**
  - `close(mut &T): Result[bool]` borrows the handle, so you can `defer c.close()`.
  - `close` zeroes the pointer word before the C release, and a second close returns `ok(false)`.
  - There is no release on drop, no destructor, and no compile-time must-close. Dropping an open
    handle leaks it.
  - Under `OBO_CHECK=1`, a leaked handle is reported at normal end with exit 8.
  - If compile-time must-close is ever wanted, it applies to files and C handles together, never to
    C handles alone.
- **D1:** body-less `native fn` declarations and `type X = foreign "C"`, with `foreign` reserved.
- **Defaults:**
  - D4: exit codes 11 (native contract), 7 (use after close), 8 (leak), 4 (census underflow);
  - D14: `sqlite3_close_v2`;
  - D16: `is_open`;
  - D6: the `.sym` bump is deferred;
  - D8: generic list kinds;
  - D12: no renames;
  - D13: keep `connect_timeout`.
- **Int-form `__cinterop`** (all-i64 arguments) stays. Programs that use it must keep compiling to
  identical bytes.

## How the work is run

The user's standing rules:

1. **Orchestrate; don't implement.** Subagents do the work. The coordinator plans, dispatches, checks
   gates, merges and promotes.
2. **Use the model the user names for every subagent**, passing it explicitly. The user selected
   DeepSeek V4.1 Flash (`deepseek/deepseek-flash`) on 2026-11-01. If a task looks too hard for that
   model, tell the user. Don't switch models silently.
3. **One branch and worktree per stage**, cut from current `main`, named `cinterop-v2-<stage>`, under
   `.claude/worktrees/`. Run stages one at a time, since each builds on the last.
4. **Loop for each stage:**
   - an implementation agent;
   - a separate, read-only review agent that tries to break it with probes and mutations;
   - findings go back to the implementer;
   - re-review until the verdict is APPROVE or APPROVE WITH NITS.

   Never merge an unreviewed branch. Stage (a) needed two fix rounds.
5. **Don't commit `obo/seed/obo` on branches.** After merging to `main`:
   - run `make promote` once (it covers the fixed point and `run_fork`);
   - run the full checks (below);
   - commit the seed in its own commit.
6. **Git:**
   - merge with `--no-ff`;
   - never push;
   - never use a bare `git stash`;
   - commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
7. **Leave the untracked files at the top of `main` alone:** `app.obx`, `build.log`, the libssl `.deb`,
   `out/`, `parse_*`, `probe.c`, `probe.cbind`. They belong to the user.
8. **Scratch output** (`TMPROOT`, `TMPDIR`, probes) goes in the session scratchpad, never in the repo.
   Test runs leave `obo/cmd/*.sym` files behind in worktrees. Remove them with `git clean -- '*.sym'`.

## Gates

Each stage branch must pass these before review and again before merge:

| Gate | Command (from the repo root unless noted) | Expected |
|---|---|---|
| Build and fixed point | in `obo/`: `make obo`, then build stage 2 and stage 3 and `cmp` them | identical |
| Byte identity | compile every `.obx` without `__cinterop` with the base compiler and with HEAD | `diff=0` (727 programs at stage (a)) |
| Fork suite | in `obo/`: `python3 tests/run_fork.py` | `1809 passed, 0 failed` or more |
| cinterop | `python3 obo/experimental/cinterop/tests/test_cinterop.py` | `cinterop tests ok` |
| db adapters | `python3 obo/experimental/db/tests/test_db_adapters.py` | `db adapter tests ok` |

After merge, on `main`, also run these from `obo/`:

- `python3 tests/imports.py`;
- `python3 tests/stdlib.py`;
- `python3 tests/module_test.py`;
- `make tools-check`;
- `python3 tests/eza.py`.

`make eza-check` fails on this host because the reference `eza` isn't installed. That failure is
environmental; the golden-file run is the gate.

**Live PostgreSQL.** The user has authorised SELECT-only live tests against the local socket. The
coordinator runs them on the branch before merging and again on `main` after promoting:

```
S=<scratch dir>
TMPROOT=$S TMPDIR=$S \
CINTEROP_TEST_DSN='host=/var/run/postgresql port=5432 dbname=postgres user=chris connect_timeout=3' \
  python3 obo/experimental/cinterop/tests/test_cinterop.py
# same environment:
python3 obo/experimental/db/tests/test_db_adapters.py
```

Expect `live libpq: ok`, `live postgres SELECT-only smoke: ok`,
`live postgres execute_sql SELECT-only vectors: ok` and `live postgres decimal SELECT-only vectors: ok`.
Agents leave `CINTEROP_TEST_DSN` unset.

## Remaining stages

Stages (a)–(g) are merged. Stage (h) is the last one:

| Stage | Scope | Checker exposure | Risk | Coordinator notes |
|---|---|---|---|---|
| (h) skill and templates | `skill/SKILL.md`, `db-adapters.md` and the project templates for explicit close, out records, `release_result`, the kinds and the `cres` policy; docs status sweep | none | low | In progress on `cinterop-v2-h`. zlib stays on its adapter; no production code is deleted. |

The full scope and gates for the merged stages are in design §8; the test lists
are in §9. Notes for historical reference:

- (b)/(c) landed together in `85923af`; (d) in `644d920`; (e) in `ef11ed7`;
  (f) in `49ae601`; (g) in `65492c0`.
- (d) deferred `out_own`/`out_offset` to (f), where SQLite needed them.
- (e) retained the old adapter for `db/postgres` until (g) deleted it.
- (f) recorded the one deviation: an out_offset-only `var` is freed at scope
  end, so the sketched exit 5 is unreachable.
- (g) moved the decimal validation into obo and added the spec-drift guard.

## Stop and ask the user when

- The chosen subagent model can't get stage (d) through review. It touches the type checker and the
  record kinds. The user said to report this rather than force it or switch models.
- A change would alter the approved design: close semantics, the exit codes, the kind list, or the
  sidecar formats beyond what §8 says.
- A test needs a live database other than SELECT-only against the local socket, or anything is about
  to be pushed.
- The cleanup audit proposes deleting files. Show the user the list first.

## Deferred follow-ups from stage (a)

These are known, safe, and documented. Fold them into a later stage.

- The `ctypes` parser refuses some valid C spellings: `long int`, `short int`, `unsigned long int`,
  plain `unsigned` and `signed`, unprototyped `void (*)()`, typedef'd function-pointer names, and
  nested function-pointer parameters. Normalising word order would fix most of them. Document the
  nested-callback limit.
- Constants are capped at 17 digits in both the compiler and `cinterop.py`, so `INT64_MAX` and
  `UINT64_MAX` can't be passed as `const:`. This is documented.
- Exit-11 paths don't free scratch, because the process exits. This is documented.
- The register-string guard in `nativeKindCall` isn't covered by any test.
- No ASan run covers the obo image itself. Scratch use is checked by the `obo_ci_scratch_live()`
  counter.
- Merged worktrees can be removed once the user agrees: `cinterop-boundary`, `cinterop-review-fixes`,
  `cinterop-v2-a`, `cinterop-v2-d`, `cinterop-v2-e`, `cinterop-v2-f` and `cinterop-v2-g`.

## Key files

- Compiler: `obo/src/obo.obo`. Kinds codegen is around `nativeKindCall`; the sidecar writer is near
  the end of the file.
- Tool and runtime glue:
  - `obo/experimental/cinterop/cinterop.py`: spec, `ctypes`, thunks, signature check, manifest;
  - `host.c`: the `obo_ci_*` scratch helpers;
  - `link.ld`;
  - `README.md`: the argument kinds reference.
- Tests:
  - `obo/experimental/cinterop/tests/test_cinterop.py`, with fixtures `bytes_fixture.*`,
    `nullconst_fixture.*`, `stack_args_fixture.*` and `stack_args_wrap.obx`;
  - `obo/experimental/db/tests/test_db_adapters.py`.
- Replaced or removed:
  - `toolkit/libpq_adapter.*` and the libpq example adapter (stage (e); the
    toolkit copy was deleted in stage (g) after the postgres port);
  - `db/sqlite/sqlite_adapter.c/.h` and `sqlite_adapter_test.c` (stage (f), deleted; the helper is `sq_stmt_errcode.c`);
  - `db/postgres/postgres_adapter.c/.h`, `db/tests/postgres_adapter_test.c`,
    `tests/fake_adapter_test.c` (registry part moved to `tests/cres_test.c`) and
    `postgres_fake_sql.cbind` (stage (g), deleted).
- `cres` users after stage (h): the zlib adapter, `examples/buffer.obx` and
  `tests/cres_test.c`. The skill templates moved to v2 foreign handles, and
  `sys`/`tls`/`http2` use `sysregistry`. No production code was deleted in
  stage (h).
- Superseded design: `native-c-interop-design.md`. Its "Superseded direction" note points to the v2
  design.
