# v7 final focused review — obo stdlib/eza design

## Verdict

**APPROVE.**

The v7 design at `/tmp/obo-stdlib-eza-design-v7.md` (4,357 lines, 285,109
bytes, sha256 `89c6a980e60e6482191f17bf46acff30c900bab8d7702764cea17932aaef6345`)
resolves both v6 blockers and folds in the stated non-blocking corrections
without introducing a new blocking contradiction. The repository was not
modified: HEAD remains `84558da`; the only worktree entry is the pre-existing
untracked `deepseek.sh`.

Scope followed the request: only the two v6 fixes, the listed precision
corrections, and contradictions they could introduce. No previously settled
design was re-opened, no heavy suites were run, and no broad new eza probes
were made. The eza behavior used below is the previously recorded P0/source
observation, not a new claim of v7 verification.

## Blocker 1 — `../tar get`: resolved

v7 consistently makes `../tar get` a broken target:

- Changelog (§1.2a): `../tar get` is resolved as a broken target, with no
  sibling `tar get` in the case directory's parent, and is never claimed
  valid.
- §7.6: the fixture owns the case directory, creates no sibling in the case
  directory's parent, asserts absence there, and expects
  `link -> '../tar get'` (whole raw target escaped with the outer style), with
  no marker even under `-F=always`.
- §7.6/§7.9/§9.2/§9.3/§3.3a/Appendix B/E/F: the same broken expectation is
  repeated; `/tar get` remains broken (`'/tar get'`, no marker, not a
  root-parent case); valid whole-parent quoting is covered by the in-fixture
  `a b/target` -> `'a b'/target`, and broken whole-raw quoting by
  `a b/missing` -> `'a b/missing'`.
- Searching all `../tar get` occurrences found no stale valid expectation
  such as `../'tar get'` or a sibling fixture. The only negative mention is
  the changelog and the explicit "do not expect a valid `../tar get`" prose.
- The observation matches eza 0.23.3: a broken target displays the whole raw
  target with the outer style and no marker; `a b/target` displays the
  whole-escaped parent plus the `QuoteSpaces` name.

The parent-content dependency is controlled by the "no sibling, asserted
absent" fixture rule. This is sound provided the case directory's parent is
the fresh test temp directory rather than a shared host directory; see the
non-blocking note 3.

## Blocker 2 — unknown-name split/no-split reporting: resolved

v7 replaces the false "unknown names always whole original token" rule with
the exact `split_on_equals` behavior and six exact cases, repeated in
§5.6, §3.3a (parser row), §7.10 (worked spellings and exact error wording),
§3.4 probe list, and the exact-error tests:

- no split, reported whole: `--foo=` -> `Unknown argument --foo=`;
  `--=x` -> `Unknown argument --=x`; `--all=` ->
  `Unknown argument --all=`;
- split long, only the name before the first `=`: `--foo=bar` and `--foo==`
  -> `Unknown argument --foo`;
- short cluster, first unknown short byte: `-Qfoo` and `-Q=foo` ->
  `Unknown argument -Q`; the rest of the cluster/split value is not part of
  the reported attempt.

These match the recorded eza observations and the source rule (split only
when the part before the first `=` is non-empty and at least one byte
follows). The related edges are consistent, not contradictory:

- `-s=` does not split; the required `-s` consumes `=` as its value, and the
  sort deduction rejects it as a bad value: the exact wording in §3.3a, §5.6,
  §7.10, and the exact tests is `Option --sort (-s) has no "=" setting
  (choices: ...)`, not "needs a value"; the "bad value" phrases describe the
  same mechanism. `--sort==` reaches the same wording.
- `--classify==` splits with value `=` and gets
  `Option --classify (-F) has no "=" setting`; `-F=` does not split and gets
  `Flag -F cannot take a value`; `-a=` gets `Unknown argument -=`; `-I=`
  consumes `=` and is later rejected as unsupported; `-lF=always` is
  accepted.
- No stale "always whole original token" statement remains outside the
  changelog's negative description.

## Precision corrections and retained baseline

The v7 diff and targeted searches confirm the stated corrections:

- Appendix A and §13 qualify `A`/`a` and `README`/`readme` as Equal only under
  `natcmpFold`; `natcmp` gives Less. `pic4`/`pic 4` and `a`/` a` remain Equal
  under both, and §7.5/§9.4 keep whitespace/case ties live-only and committed
  names comparator-distinct.
- The `-aa` `.` extension key is `File::ext(dir.path)` (None for operands
  `.`, `..`, `/` and paths ending in `..`), explicitly not the shorthand
  `ezaExt(lastComponent(operand))`; operand `.` is recorded as observed
  while `..` and `/` remain source/P0.
- `--help`/`--version` are excluded from the E0 parser and from the
  "rejected eza-valid flags" rule; they are ordinary unknown names
  (`--help`/`--version` whole; `--help=x`/`--version=x` ->
  `Unknown argument --help`/`--version`), exit 3, with E0 printing neither
  help nor version. §7.11, §5.6, and §7.10 agree.
- `SIZE_MUTANTS` is stated as four caught and one equivalent in §2.1, the
  gate lists, §12.1, and Appendix; no stale five-caught claim remains.
- The end note identifies v7 and the two fixes; no stale v6 status line
  remains.
- The `84558da` baseline is retained exactly: source 10,448 lines /
  382,329 bytes (sha256 `44770afa…`); `seed/obo` == `obo` == 421,889 bytes
  (sha256 `55dab101…`); peak 1,636,176 live / 35,076,608 allocated /
  363,824 spare; `run_fork` 1,130; `mutate_fork` 527/549 + 22;
  `mutate_port` 495/495; tools 36,865 / 40,961 / 45,057; minimal 28,673;
  `port_compare` 1,147 / 1,025; `port_test` 486; `sym_test` 27; `sym_fuzz`
  200; `doc_check` 45; `elf_test` and the five `SIZE_MUTANTS` unchanged.
  References to `6214158` are the design anchor/history, not the baseline.

## New contradictions checked

No new blocking contradiction was found. Targeted checks of the touched
rules and their repetitions (parser, symlink display, target path,
comparators, dot key, help/version, mutation counts, baseline) found the
same behavior in each place. The `../tar get` cleanup did not leave a valid
expectation in the fixture list, exact cases, gate list, or appendices; the
unknown-name cleanup did not leave the old whole-token rule; the `-s=`
mechanism and its exact error text are consistent.

## Non-blocking notes (do not require a v8 before implementation)

1. **§5.6 wording.** The first clause says "a token that is **not split** is
   reported whole (`--foo=`, `--=x`, `--all=`)". A short cluster such as
   `-Qfoo` is also not split, yet the same bullet's short-cluster clause and
   all six exact cases correctly say `-Q`. The first clause should read "a
   **long** token that is not split" (or "a non-cluster token") to avoid a
   literal implementation reporting `-Qfoo` whole. This is a wording
   ambiguity, not a behavioral gap: the exact rule and tests are explicit.

2. **Optional valid `../`/`./` target coverage.** With `../tar get` broken and
   no parent sibling, the only `../` target is broken; §7.9's "Consequences
   to test" still mentions resolved-path classification for `../`, `./`, or
   multiple components, and the valid multi-component case is `a b/target`.
   If full valid-`..` coverage is wanted without a parent sibling, add an
   in-fixture valid target such as `sub/../target` (with `sub/` and `target`
   inside the case directory; display `sub/../target`, classification
   through the resolved path), or a valid `./target`. This is a coverage
   enhancement, not a blocker; the `-d sub/link` cwd-decoy cases and
   `a b/target` already exercise the core target-path base.

3. **Fixture parent.** The `../tar get` broken case depends on the case
   directory's parent having no `tar get`. The design says the fixture owns
   the case directory and asserts absence in the parent, but the parent
   should be explicitly a fresh private test temp directory (or the fixture
   should own a per-case parent) so a host `/tmp/tar get` or a parallel case
   cannot make the target valid or make the absence assertion fail. The
   design's "fresh temp directory" harness wording likely already implies
   this; stating it would make the broken status deterministic. Non-blocking.

## Conclusion

Both v6 blockers are correctly fixed, the listed precision corrections are
present, and no new blocking contradiction was introduced. APPROVE.
