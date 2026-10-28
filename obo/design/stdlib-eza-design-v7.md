# CLI stdlib as modules and the `eza` core — design note v7

Status: design only, revision of `/tmp/obo-stdlib-eza-design-v6.md` after the
final independent v6 review in
`/tmp/obo-stdlib-eza-design-v6-review.md` (VERDICT: REVISE; two blocking
findings plus precision notes). The v6 baseline (main `84558da`, size-fixes
merged at `134e6a5`, seed promoted) is correct and is kept; the size-fixed
baseline and every prior fix are retained.
Read-only against
`/home/chris/obx`: **main is `84558da`**, the size-fixed tree (size-fixes merged
at `134e6a5`; seed promoted at `84558da`; design anchor `6214158`, tools-modules
merge `4e733a6`). The compiler source is 10,448 lines / 382,329 bytes (sha256
`44770afa…`); `seed/obo` == `obo` == 421,889 bytes (sha256 `55dab101…`), and the
fixed point is seed == obo == stage 2 == stage 3. The size fixes changed
`src/obo.obo` by 24 lines (the runtime-data segment's file size is its nonzero
prefix; the epilogue is `leave; ret`), added `tests/elf_test.py` and five size
mutants, and promoted the seed; the runtime itself is unchanged. The Stage 2
module/`.sym` surface, library, tool and builder state are still the merged
tools-modules state, so the v4/v5/v6 design claims hold except where the v7
corrections below apply. No repository, branch, worktree, seed,
memory, permission or config change was made by this pass. The only writes are
this file and a scratch patch helper under `/tmp`; no repository file, branch,
worktree, seed or configuration is touched.

## Changelog v6 → v7

- `../tar get` resolved as a **broken target** (v6 review blocker 1). The
  fixture owns the case directory only, creates no `tar get` sibling in the
  case directory's parent, and asserts absence there, so the target metadata
  lookup fails and eza displays
  `link -> '../tar get'` — the whole raw target escaped with the outer style —
  with no marker even under `-F=always`. Valid whole-parent quoting is covered
  by the relative valid target `a b/target` → `link -> 'a b'/target`; broken
  whole-raw quoting is covered by `a b/missing` and `../tar get`. Updated
  §7.6, §3.3a, §7.7, §7.9, §9.2, §9.3, Appendix B/E/F, the fixture-ownership
  sentence and the probe list; the `../'tar get'` valid-target expectation is
  removed.
- Unknown-name reporting corrected (v6 review blocker 2). eza reports the
  **looked-up name**, not always the whole original token. A token that does
  not split is reported whole (`--foo=`, `--=x`); a split long token reports
  the name before the `=` (`--foo=bar`, `--foo==` → `--foo`); a short cluster
  reports its first unknown short byte (`-Qfoo`, `-Q=foo` → `-Q`). The false
  "always the whole original token" rule is replaced in §5.6; the six cases
  are added to §5.6, §3.3a, §7.10 and the exact-error tests (and the §3.4
  probe list).
- Precision corrections: Appendix A and §13 qualify `A`/`a` and
  `README`/`readme` as Equal only under `natcmpFold` (`natcmp` gives Less);
  §3.3a marks only the operand `.` dot key as observed (`..`/`/` are the
  source rule/P0); §7.11 (and §5.6/§7.10) state that `--help`/`--version` are
  not in the E0 parser and take the unknown-name path, including
  `--help=x`/`--version=x`; §2.1 (and the gate lists) state that the five
  `SIZE_MUTANTS` are four caught and one equivalent; the end note states v7
  instead of claiming the v5 review was fully applied while the v6 blockers
  existed.

## Changelog v5 → v6

- `/tar get` corrected (v5 re-review blocker 1): it is an unrealizable valid
  target in a normal test (a test cannot create files at `/`), so eza treats a
  symlink to it as **Broken** and prints `spaceabs -> '/tar get'` with no
  marker, even under `-F=always`. It is removed from the valid root-parent
  cases and from every listing/probe expectation (§3.4, §7.6, §7.9, §9.2,
  §9.4, §12.3, Appendix B/E/F). The valid root-parent coverage is `/tmp`,
  `/tmp/` (`-F=always`) and `/` (`//` with the marker). A broken-target live
  case (`link -> '/tar get'`, no marker) and its §3.3a observation are added;
  the `parentPrefix` primitive test input is now `/tmp/x y`.
- Empty-value `=` splitting corrected (v5 re-review blocker 2): eza's
  `split_on_equals` is applied to **short and long** tokens and splits only
  when the name before the first `=` is non-empty and at least one byte
  follows it. The v5 "short cluster splits at the first `=`" and "a long token
  ending in `=` is unknown" heuristics are replaced by the exact rule in
  §5.6, with `-s=`, `-I=`, `-a=`, `-F=`, `--classify==`, `--sort==`,
  `--classify=`, `--all=` and `--=x` cases in §5.6, the exact-error tests,
  §7.10, §7.11 and §3.3a.
- `-aa --sort=extension` dot key defined as **`File::ext(dir.path)`** (the
  extension of the operand path's final normal component; `None` when
  `Path(operand).file_name()` is `None`), not the literal
  `ezaExt(lastComponent(operand))`: `.`/`..`/`/` give `None`, `dx.x` gives
  `Some("x")`, `dx.` gives `Some("")`. Updated §1.1, §1.2a, §3.3, §3.4,
  §7.3, §7.5, §9.4, §12.1, §12.3, §13 and Appendix F; added an operand-`.`
  probe and case.
- Port peak and `run_fork` are baselines in §11.2/§11.4, not post-feature
  promises: `run_fork` is "1,130 baseline plus the new cases, all pass" and
  the port gate is "live peak < 2,000,000; record the measured peak (current
  baseline 1,636,176)".
- Mutant-anchor precision: new `readDirFd` runtime code must stay textually
  distinct from `mutate_io`'s short-read/fd-table anchors or the anchors are
  re-anchored in the same commit, with the measured count recorded (§6.8,
  §10.3, §11.4); the `mutate_meta` compiler-runner "fd not materialised"
  killing case is made explicit (`max` expression mutating the fd through a
  `mut &int`), and "path not pinned" may be recorded as equivalent with the
  reason (§6.8).
- Comparator wording tightened: committed name/type/extension (including
  `-aa`) goldens need pairwise comparator-distinct sorted names, while
  size/date long goldens need only distinct sort keys; `A`/`a` and
  `README`/`readme` are live-only under `natcmpFold` (they are distinct under
  `natcmp`), and whitespace-equivalent pairs are live-only under both
  (§7.3, §7.5, §9.4, §12.1, §12.3).
- Minor precision: "ran the binary once" is corrected to the independent
  reviews' several throw-away probes (§3.1, §3.3, §3.3a, Appendix F); §2.1's
  `case_lib` prose now matches §9.8 (absolute `LIBDIR`); `elf_test` mentions
  its normal-program run; P2 records measured mutation/doc-check counts if
  they change (§10.3).

## Changelog v4 → v5

- Baseline refreshed to main `84558da` (size-fixes merged at `134e6a5`; seed
  promoted): compiler source 10,448 lines / 382,329 bytes (sha256 `44770afa…`);
  `seed/obo` == `obo` == 421,889 bytes (sha256 `55dab101…`); fixed point seed
  == obo == stage 2 == stage 3; peak 1,636,176 live / 35,076,608 allocated /
  363,824 spare, 0.11 s; run_fork 1,130; mutate_fork 527/549 + 22 equivalent;
  mutate_port 495/495; tool binaries 36,865 / 40,961 / 45,057; minimal 28,673.
  `tests/elf_test.py` and five size mutants (`SIZE_MUTANTS`) are added to the
  gate lists; the size fixes are the zero-tail runtime-data segment and
  `leave; ret` epilogue. All baseline figures and gate lists below are updated.
- Comparator ties closed (v4 review blocker 1): every committed name/type/
  extension (including `-aa`) golden now requires pairwise comparator-distinct
  names under the comparator used (`natcmpFold` for default/name and
  lowercase extension; `natcmp` for `Name`, `Ext`/`Extension` and type);
  case-only and whitespace-equivalent pairs are
  live-only, and eza's stable sort over readdir order is the global tie rule
  (§1.2a, §3.3, §7.3, §7.5, §9.2, §9.4, §12.1, §12.3, Appendix A/F).
- `-aa --sort=extension` dot key corrected (blocker 2): the `.` key is the
  operand path's `File::ext` (`d` → None, `dx.x` → Some("x")); `..` is
  None; the "final component has no dot" pinning condition is removed
  (§1.2a, §3.3, §7.3, §7.8, §9.4). (v6 refines the shorthand to
  `File::ext(dir.path)`; see the v6 changelog.)
- Target parent root branch added (blocker 3): one parent-prefix primitive
  emits a single `/` for the root parent; `/tmp`, `/tmp/` and `/` with and
  without `-F=always` are live exact cases (§3.3, §7.6, §7.9, Appendix B).
  (v5 also listed `/tar get`; v6 corrects this — it is a broken target, not
  a root-parent case.)
- `tests/mutate_meta.py` split into a mutated-runtime/`entry.S` runner and a
  mutated-compiler runner with named corpora; `-d sub/link`, target-is-symlink
  and chain/loop cases move from P3 `tests/meta/*` to P4 `tests/eza.py`
  (§6.8, §11.1, §11.2, Appendix E).
- Full compiler gate now includes `tests/sym_test.py` 27/27 and
  `tests/sym_fuzz.py` 200/200, and the `OBO_VERSION` bump checklist includes
  `tests/sym_test.py`'s hardcoded `OBO_VERSION = '0.2'` and literal
  `compiler "0.2"` expectations (§6.9, §10.5, §11.2, §11.4, Appendix E).
- Non-blocking corrections: cat's exact `strings` use; `errors::text` adds 34
  only (40 already present); pinned version output is `v0.23.3 [+git]`;
  parser option interspersing after free operands, empty-equals behaviour and
  mixed `-lF=always` clusters; explicit lstat operand existence (dangling
  operands list); metadata probe runs a slot; real `run.compile_with` helper
  (no global `module_args`); `write_padded` keeps its `io` import; the
  `writeByte`/`fmt`/`errors` mutation boundary stated; P2 docs list;
  `tests/sym_test.py` ownership; stdlib test list; argv[0] wording;
  `-I lib` cwd.

## Changelog v3 → v4

- Rebased on merged main `6214158`: §2.1, §4.3, §10.1, §10.3, Appendix D and
  every `182c60c` reference now describe the merged `lib/{io,strings}.obx`
  modules, the module-root `programs/` tools, the non-concatenating builder
  with private `<out>/sym` and `_map_error`, the mutated-module-tree
  `mutate_io.py` and the qualified `tests/lib`/`tests/run/io_*` roots.
- P1/P2 ownership rewritten (blocker 1): P1 edits the existing
  `lib/strings.obx` in place, adds the §5.1 API and keeps
  `cmpBytes`/`errText`/`digits`/`appendPadded` as temporary compatibility
  functions; P2 removes them in the same change that adds `fmt.obx`,
  `writeByte` and `errors::text` and moves the tools to `cmd/`. `_map_error` is
  kept. The P1 gate builds the tools and runs the 199/199 differential.
- `-aa` host-golden leak closed (blocker 2): only name/type/extension sorts may
  be committed name-only (v5 adds the pairwise comparator-distinct
  requirement); `-aa --sort=size`/`--sort=modified` are live/shape-only or
  need a pinned directory and parent plus a manifest (§7.3, §9.4, §12.3 item
  10, Appendix F).
- E0 symlink target metadata specified (blocker 3): a relative target resolves
  against the entry's directory or the operand's parent for `-d`; absolute
  targets as written; classification/broken detection uses stat (follow), while
  the link's own cells use lstat; new `-d sub/link`, target-is-symlink and
  chain/loop cases (§7.9, §7.7, Appendix B, §9.2).
- Whole-parent quoting corrected (blocker 4): the parent string is escaped once
  with the outer style, not per component, for operands and symlink targets
  (§7.2, §7.6, Appendix B, §9.2).
- Runtime metadata mutants get a named runner and honest counts (blocker 5):
  `tests/mutate_meta.py` covers `statPath`/`lstatPath`/`readLinkPath`/
  `readDirFd`, `rt_meta_test.c`, `tests/meta/*`, `entry.S` and, in P5a/P6a,
  `isatty`/`getenv`/`terminalWidth`; gates state the actual counts, and
  `mutate_io` remains 27/27 for I/O (§6.8, §11.2, §11.4).
- Budget cut order fixed (blocker 6): `readLinkPath` cannot be cut while E0
  symlink targets remain; the cut is stop-and-report or an explicit roadmap
  stop-condition violation (§6.8, §12.2).
- Non-blocking corrections: `--sort=default` removed as a value; one `build`
  entry point; `join("a","")` = `"a/"`; corrected `try` wording; doc-example
  gate counts; `cmd/` resolved with churn as the user decision; exact
  `tests/lib` names; bare `--color`; `--time-style` with a non-long view;
  success-only exit-3–9 defect scan; build-example cwd; extra env vars pinned;
  stale line references replaced by names; `mutate_io` P2 wording changed to
  "already implemented"; stale seed 415,456 correction removed; `-aa
  --sort=extension` explained; merged gate figures (1147/1147, 1025/1025,
  199/199, 27/27, 45 examples) used throughout.

This note targets the **current size-fixed main's Stage 2 surface**, not the
branch assumptions of v3/v4. Section 2.3 records the observed CLI, `.sym` rules, versions and exact
messages from `obo/LANGUAGE.md`, `obo/FORK.md` and the merged tests. The eza
reference is still the pinned v0.23.3 release asset (section 3). The
independent v4 review, the focused v5 re-review and the v6 review ran that
binary in several throw-away probes and their observations are recorded as
review evidence (§3.3a); this design pass did not re-run it, and every byte a
test is gated on remains a P0 probe/record.

Three kinds of statement are marked throughout:

- **Verified** — read in the current tree at `84558da` (size-fixes merged at
  `134e6a5`; design anchor `6214158`; size-fixed compiler and seed), in the
  approved modules design, or in the pinned eza 0.23.3 source/release asset.
  The independent reviews' throw-away-probe observations (§3.3a) are
  evidence, not committed goldens.
- **Design rule** — a decision this note makes and tests must enforce.
- **Probe** — a behaviour not yet committed or re-recorded by P0. §3.3a's
  observations came from the independent v4, v5 and v6 review probes and are
  P0 work, not facts; no exact eza output byte is a committed golden before P0
  records it.

The eza reference used for source review is the tagged release **v0.23.3**
(2025-09-14). Its release tarball was downloaded and hashed during this design
pass (section 3); the binary was not run by this design pass. The independent
v4 review, the v5 re-review and the v6 review ran the cached pinned binary in
several throw-away probes (the observations in §3.3a); those observations are
evidence to be re-recorded by P0, not committed goldens.

---

## 0. Recommendation in one page

Build a small dependency-leaf stdlib as modules under `obo/lib/`, move the
tools to `obo/cmd/`, add **four** runtime metadata builtins for eza E0
(`readDirFd`, `statPath`, `lstatPath`, `readLinkPath`), then implement the
non-TTY core of eza: default/`-1` one-entry-per-line output **including symlink
targets**, the exact hidden/`-a`/`-A` rules, natural case-folded name sort, the
other E0 sort fields, post-sort reverse, directory grouping, quoting, `-F`,
`-D`/`-f`, `-d`, and a deliberately restricted long mode. Colour and tree are
E1; grid and UTF-8 width are E2. `currentDir`, `isatty`, `getenv` and
`terminalWidth` are deferred to later compiler features (P5a/P6a), not bundled
into the E0 feature.

The single largest external blocker is the eza reference. This design pins
**eza v0.23.3**, x86-64 Linux GNU release asset, tarball sha256
`02e818f7b06f2c637b25761731104f1e9d6ea8f6e5924ab05eae69db4ed072a5` and binary
sha256 `10457dde35808517d09bcd3bfb6bc386000d06c2126e34b9071e09470a8f7cd8`.
P0 must install it, verify the digest, run the probe, and commit `VERSION` and
`REFERENCE.md` before any eza implementation. If the user will not provide a
reference, the target degrades to own goldens; that violates the roadmap's
differential stop condition and is the user's decision, not a silent fallback.

The design is implementable without a compiler or runtime redesign and without
raising `PEAK_BUDGET` (2,000,000 bytes). The E0 compiler feature adds one
four-argument syscall helper (`sys4` with `r10`) and four builtin branches; the
measured main-tree live peak is 1,636,176 bytes, leaving 363,824 bytes. Measure
after each compiler feature; the cut order is in section 6.8. No new descriptor
kind, `.sym` grammar change or ABI bump is required; `OBO_VERSION` bumps with
each compiler feature.

---

## 1. Review resolution

### 1.1 Blocking findings

| # | Finding | Concrete rule in v5 | Section |
|---|---|---|---|
| B1 | `import "sort.obx"` resolves to the tool itself | All tools live in `obo/cmd/`; every tool imports the stdlib by explicit relative path (`../lib/io.obx`, …). The sort tool **does not import `sort`** (it does not use it). eza imports `../lib/sort.obx`. A compile matrix compiles every tool and every cross-tree import, and a resolver test pins the collision behaviour. | 4.1, 4.2, 10.1 |
| B2 | P1 and P2 both create `lib/strings.obx` | On merged main the file already exists. **P1 edits it in place**: it adds the §5.1 API and keeps `cmpBytes`, `errText`, `digits` and `appendPadded` as temporary compatibility functions (the merged tools call them). **P2 removes those four** in the same change that adds `fmt.obx`, `writeByte`, `errors::text` and moves the tools to `cmd/`. There is no create, rename or delete of `lib/str.obo`/`lib/io.obo` (those files no longer exist). P1 merges first; P2 edits `strings.obx`/`io.obx`, never renames. | 4.3 |
| B3 | `env::string` shadows the `string` type/conversion | The helper is **`env::value`** (optionally `env::getOr`); `string(...)` remains the conversion and `string` the type. `env::has` uses a local (`let v = get(name); return v != nil`). A compile test puts `value`, `getOr`, `string(...)` and `string`-typed declarations in one module in several orders. | 5.8 |
| B4 | `-a` semantics wrong | Count `-a`/`--all` occurrences (each byte in a cluster counts). `-A`/`--almost-all` is a boolean. c=0,!A → hidden excluded; c=1 or A (with c≤1) → dotfiles, no dots; c≥2 → dotfiles **and** synthesized `.`/`..`. `-A -aa` shows dots (source: any c≥2). Kernel `.`/`..` are always discarded; when enabled they are synthesized first. Fixtures and goldens updated. | 7.3 |
| B5 | Default name sort is natural/case-mixed, not byte order | `strings::natcmp`/`natcmpFold` implement the **natord-plus-plus 2.0.0** algorithm (the crate eza 0.23.3 uses), ASCII subset in E0. Default is `Name(AaBbCc)` = case-folded natural. `--sort=Name` is case-sensitive natural. The design never claims byte parity with byte order; fixtures for exact ordering are ASCII. | 5.1, 7.5 |
| B6 | Eza quotes names with spaces by default | Implement eza's `QuoteSpaces` rule exactly: quote if the name contains a space or apostrophe; double quotes if it contains an apostrophe, else single; control bytes `<0x20` and `0x7f` are escaped with Rust `escape_default` semantics; embedded quotes are not escaped. Applied to default/long/tree names and always to symlink targets. Exact control-byte and quote fixtures. | 5.1, 7.6 |
| B7 | `-F/--classify` takes an optional WHEN; `FlagSpec` cannot express it | `cli::FlagSpec` gains a value mode (none/required/optional), consume/accept/choices lists and a default. E0 `-F` with no value is `auto` (TTY-only; E0 non-TTY therefore no markers), `-F=always`/`-Falways`/`-F always` force markers, `--classify=always` likewise. `--color`/`--icons` use the same optional machinery; E0 accepts bare `--color`/`--colour` (= auto, no colour in non-TTY E0), `--color=auto`, `--color=never`, and bare `--icons`/`--icons=auto`/`--icons=automatic` as a no-icons no-op plus `--icons=never`; `--icons=always` is rejected. Spellings pinned from the 0.23.3 parser. | 5.6, 7.7 |
| B8 | `-d` is `--treat-dirs-as-files` | Canonical `-d, --treat-dirs-as-files`; `--list-dirs` is an accepted alias in 0.23.3 (flags.rs defines it and dir_action checks either). Probe aliases; flag table/tests/docs use the canonical spelling and test the alias. | 7.11 |
| B9 | Exit codes wrong | eza-shaped constants: `EXIT_OK=0`, `EXIT_FAIL=1`, `EXIT_PATH=2` (input metadata failure in 0.23.3 source), `EXIT_USAGE=3`, `EXIT_PERMISSION=13`. Usage is **3**, not GNU coreutils' 2. Permission/I-O statuses are probed per class; the man page's 13 is not assumed to be the process status where the source branch continues. Own usage errors use 3 and own goldens. | 5.7, 7.10 |
| B10 | Restricted long shape unverified/wrong | Pinned 0.23.3 columns for `-l -n --bytes --time-style=long-iso` are **permissions, size, user, modified date, name** (no links, no group by default; `-H` is opt-in and not supported in E0). Permissions start with eza's type char (`.`, `d`, `l`, pipe `\|`, `s`, `b`, `c`, `?`), not ls's `-`/`p`. Non-regular size is `-`. Widths are per-column maxima, each cell followed by one space; size right-aligned, user and date left-aligned. A probe confirms. `-l --no-user --bytes --time-style=long-iso` is the host-independent golden mode **provided name-order goldens have pairwise comparator-distinct sorted names and size/date goldens have distinct sort keys** (B15). | 7.8, 9.4 |
| B11 | Entry paths not constructed; `bySize` stats wrong paths | Every entry operation uses `entryPath(operand, name)` via `paths::join` (stat/lstat/readlink/tree/open). `fs::Dir` carries its operand path. Tests use operands outside cwd and relative `../` operands. The joined-path limit (<4096 bytes, `cpath`) and the path race are documented; future `fstatat(dirfd,name)`/`readlinkat(dirfd,name)` is the fix. | 5.2, 5.9, 7.9 |
| B12 | No eza reference | P0 acquires/pins exact eza 0.23.3, verifies the release digest, records `VERSION`/`REFERENCE.md`/goldens, and CI installs it. `--require-reference` requires the exact version and binary hash. If the user refuses, the target downgrades to goldens and this is flagged as a roadmap stop-condition violation. Top external decision. | 3 |
| B13 | Shared sym-dir (`build/sym` in v1; `obo/.obo/sym` before the tools-modules merge) | **Merged main already uses per-output sym-dirs**: `build_tools._symdir` creates `<out>/sym` and passes it to the probe and every compile; tests use a fresh private temp sym-dir per compile; `build/tools/sym` and `build/eza/sym` are distinct. There is no global tool `SYMDIR` to remove. Sym-dirs are order-only Make prerequisites. | 10.2 |
| B14 | P2 migration incomplete | **Already implemented on merged main**: `tests/io_test.py` builds tools through `build_tools.build` with per-case private sym-dirs, all `tests/lib` roots import `io.obx` and qualify every io name, `obx/tests/run.py` has `extra_args`, `run_fork.py`/`port_compare.py` carry no `// prelude: io` branch, `tests/run/io_reader.obx`/`io_writer.obx` and `tests/lib/*.obx` are module roots with `-I lib`, and `mutate_io.py` already builds a scratch mutated module tree. v4/P2 closes only the remainder: `fmt.obx`, `writeByte`, the `errors::text`/`strings::cmp` migration, the four compatibility-function removals, and `cmd/`. Gates stay 199/199, 2/2, and byte-identical tool output. | 10.3 |
| B15 | Host-dependent long goldens | v2 found `-aa -l`, absolute operands and `-d ..`/`-d /` still host-dependent; v4/v5 exclude or pin them. **v5/v6:** `-aa` **name-only** is host-independent for name/type/extension sorts only when every pair of listed names is **comparator-distinct** under the comparator used (`natcmpFold` for default/name and lowercase extension; `natcmp` for `Name`, `Ext`/`Extension` and type); eza's stable sort preserves readdir order for equal keys, so `A`/`a` and `README`/`readme` are live-only under the fold comparator (they are distinct under `natcmp`) and whitespace-equivalent pairs (`pic4`/`pic 4`, `a`/` a`) are live-only under both. `-aa --sort=size` and `-aa --sort=modified` read the listed directory's and host parent's metadata for the dot keys and are live/shape-only (or need a pinned directory and parent plus a manifest). The `.` extension key is `File::ext(dir.path)` (`None` when `Path(operand).file_name()` is `None`; otherwise `ezaExt` of the final name), not the operand component. Live differential is primary. Committed long goldens use `--no-user` (no uid column), `TZ=UTC`, ASCII names (pairwise comparator-distinct for name-order goldens; distinct sort keys for size/date goldens), regular files with pinned sizes/modes/mtimes, and no xattrs. Directory and non-regular sizes are `-`; link counts are absent (no `-H`). Size-sort goldens use regular files with distinct sizes. `--record` refuses/normalizes absolute stdout as well as stderr; manifests cover any remaining varying data; placeholders are repadded. | 7.3, 7.5, 9.4 |

Status against current main `84558da` (size-fixes merged at `134e6a5`; design
anchor `6214158`): B1 and B3–B13 remain closed from v2.
B2 was **not** closed by v3 against the merged tree (v3's P1 would have replaced
`lib/strings.obx` and dropped functions the merged tools call); v4 closes it by
editing in place and keeping compatibility functions until P2. B14 is closed as
"already implemented, with the `fmt`/`cmd`/`errors` remainder named". B15 is
closed by the v5 comparator-distinct-name rule, the corrected
`File::ext(dir.path)` dot key and the parent-prefix root branch in
§7.3/§7.5/§7.6/§7.9/§9.4. The v3-review blockers are addressed in §7.9
(target metadata), §7.6 (whole-parent quoting), §6.8 (mutation runner/counts
and cut order) and §10.3 (rebase). The v4-review blockers are addressed in
§7.3/§7.5/§9.4 (comparator ties), §3.3/§7.3/§9.4 (dot key), §7.6/§7.9 (parent
root), §6.8 (mutate_meta split into runtime and compiler runners) and
§6.9/§10.5/§11.2/§11.4 (symbol-file gates and the version bump). The v5
re-review blockers are addressed in §7.6/§7.9/§9.2/§9.4/§3.4/Appendix B/E/F
(`/tar get` is a broken target; the root-parent cases are `/tmp`, `/tmp/` and
`/`) and §5.6/§7.10/§7.11 (exact `split_on_equals` for short and long
tokens). The v6-review blockers are addressed in §7.6/§3.3a/§9.2/Appendix B
(`../tar get` is a broken target with no sibling, displayed `'../tar get'`
with no marker even under `-F=always`) and §5.6/§7.10 (unknown names report
the looked-up name: no-split whole token, split long name before `=`, first
unknown short byte).

### 1.1a v2 review blocking findings (applied in v3; v4/v5 corrections marked)

| # | Finding | v4 rule | Section |
|---|---|---|---|
| 1 | E0 default/`-1` omitted symlink targets and put classify markers on the link | eza 0.23.3 `lines.rs` calls `.with_link_paths()`: default/`-1`/`-d` lines render `name -> target` with outer name quoting, target parent prefix with outer style, target name with `QuoteSpaces`, broken target escaped with outer style; `-F=always` appends the marker to the **target** (`link -> target/`, `link -> prog*`), no marker for broken/error targets. Grid E2 keeps the marker on the link (`link@`). Exact lines/default/`-1`/`-d` symlink cases are added. **v4:** target classification/broken detection uses the followed target path (§7.9); lstat is only for the link's own cells. | 7.1, 7.6, 7.7, 7.9, 9.3 |
| 2 | Committed `-aa -l`, absolute operand display and `-d ..`/`-d /` were still host-dependent | Committed goldens exclude `-aa -l`, absolute operands, and `-d ..`/`-d /`/`-d .`; those are live/shape only. `--record` refuses absolute-looking stdout as well as stderr. **v5:** name-only `-aa` is committed for name/type/extension sorts only when every listed pair is comparator-distinct under the comparator used; equal keys preserve readdir order. `-aa --sort=size` and `-aa --sort=modified` are live/shape-only (the dot keys read host directory/parent metadata), or committed only with a chmod/utime-pinned directory and parent plus a matching manifest that records their mode/mtime/`st_size` and filesystem. `-aa -l` needs the same pinning. The `.` extension key is `File::ext(dir.path)` (`None` when the operand has no `file_name()`). Operand-display normalization (`a//b`, `a/./b`, `dir/`, `/`, `.`, `..`) is tested. | 7.3, 7.5, 9.4 |
| 3 | `mutate_io` could not exercise library mutants after the module migration | **Already implemented on merged main** (`tests/mutate_io.py`): for each `LIB_MUTANT`, `<work>/lib` is built by copying every other `lib/*.obx`, `io.obx` is overwritten with the mutant text, the text is asserted to differ, and every affected root is compiled with `-I <work>/lib` and a private sym-dir. Runtime mutants use the real `lib/`. The P2 change re-anchors the patterns after the `strings`/`io` edits; it does not add the mechanism. | 10.3 |
| 4 | P1 gate could not run: tool matrix before tools exist, wrong collision expectation, unconditional `--sym-dir` | P1 compile matrix covers only stdlib modules, a tiny importer per module, and temporary decoy/collision trees; the tool matrix moves to P2. A headerless **imported** file → `imported module '<path>' needs a 'module Name' header`; a true self/two-module cycle → `import cycle: ...`. P1 requires current size-fixed main (Stage 2 surface) and always passes private `--sym-dir` (fallback for a Stage 1 compiler omits it). **v4:** because P1 edits `lib/strings.obx` in place, the P1 gate also runs `make tools` and the 199/199 differential (build-neutrality proven, not assumed). | 4.2, 10.2, 11.1 |
| 5 | P2 `tests/lib` qualification list was incomplete | **Already qualified on merged main.** Exact v4 names: `boundaries` (`io::newReader`, `io::readLine`, `io::readByte`, `io::takeChunk`, `io::newWriter`, `io::write`, `io::writeBytes`, `io::flush`, `io::fill`), `stream` (`io::newReader`, `io::newWriter`, `io::pump`, `io::flush`, `io::count`, `io::eof`), `short_write` (`io::newWriter`, `io::writeBytes`, `io::flush`), `write_padded` (`io::Writer`, `io::newWriter`, `io::write`, `io::flush`, then `fmt::writePadded` after P2). Boundaries does not use `io::eof`; stream does not use `io::writeBytes`. All four roots run through `io_test.py`. | 10.3 |

### 1.2 Non-blocking findings

| # | Finding | Correction in v5 | Section |
|---|---|---|---|
| 1 | `--sort=ext` undocumented | 0.23.3 source accepts `ext` and `extension` (and `Ext`/`Extension`); the man page lists only extension. Support both, record the accepted table, test, and do not rely on the man page. | 7.5, 7.11 |
| 2 | Unsupported short aliases not enumerated | Full eza 0.23.3 short+long matrix in Appendix D; unsupported forms rejected with own goldens. | 7.11 |
| 3 | `EXIT_USAGE=2` GNU-shaped | `EXIT_USAGE=3`; `errors::text(2)` remains errno text; eza constants live in `errors`. | 5.7 |
| 4 | `env::has` owned temporary | Local `let v = get(name); return v != nil`; compile/run test. | 5.8 |
| 5 | EINTR not retried | All four E0 syscalls retry `-EINTR` in loops like `io_read`/`io_write`; a gated `OBO_META_FAULT=eintr` hook injects one EINTR per syscall under `OBO_CHECK=1`; a mutant removes a retry and is killed. | 6.4, 6.8 |
| 6 | Stale-fd `isatty`/`terminalWidth` | Deferred to P5a/P6a; when added: no fd-table fatal, closed tracked fd returns false/0, ioctl may observe a reused fd; document and test pipe/file/`/dev/null`/pty under `OBO_CHECK=1`. | 8.5 |
| 7 | Long paths | `entryPath` limit documented; exact cases stay under 4095 joined bytes; future `fstatat`/`readlinkat` alternative recorded. | 5.2, 7.9 |
| 8 | `paths::ext`/`clean`/`base` assumptions | `paths::ext` is a stdlib contract with an explicit table (Rust `Path::extension` shape); eza's extension sort uses a separate `ezaExt` with eza's `rfind('.')` + ASCII-lowercase quirk. `base`/`dir`/`stem`/`clean` are removed as unused shell-shaped APIs; operand display uses `lastComponent`/`parentOf`. Probe eza path display for operands; test the stdlib table independently. | 5.2 |
| 9 | `cli::parse` bounds/optional tests | `from` bounds checked; negative/out-of-range/empty specs; optional-value spelling matrix; no-value/required-value/unknown tests. | 5.6 |
| 10 | Negative time division | `time::floorDiv`/`floorMod` correct truncation; pre-1970 tests. Eza's long format shows `-` for pre-1970 mtime (source: `duration_since(UNIX_EPOCH).ok()?`), which the eza layer implements. | 5.10, 7.8 |
| 11 | `currentDir` not needed | Dropped from v1; no slot, no `resultCall2`, no E0/E1 use. Future `--absolute` work. | 8.5 |
| 12 | `isatty`/`terminalWidth`/`getenv` E1/E2 | E0 slots 33–36 only; P5a adds 37 `isatty`, 38 `getenv`; P6a adds 39 `terminalWidth` if grid needs it. | 6.1, 8.5 |
| 13 | `readDirFd` unbounded max | Runtime cap `MAX_DIR_BUF = 1 MiB`; `max > 1 MiB` is `EINVAL` before allocation; `fs` uses 32 KiB; tests include 1 MiB and 1 MiB+1. | 5.9, 6.3 |
| 14 | Dead/shared sym-dir | **Merged main already uses per-output `<out>/sym`** (`tools/build_tools.py:_symdir`), creates it, and passes it to the probe and every compile; there is no global tool `SYMDIR`. The generic `.obo/sym` convention remains only for other Stage 2 builds/tests. Nothing to remove in P2. | 10.2 |
| 15 | Stale 954,896 comment | `port_compare.py` comment updated to 1,636,176 and the current budget rationale. | 10.4 |
| 16 | P1 env untestable | No `env.obx` in P1. The review said P3; this design defers `getenv` to P5a (E1), so P5a owns env behavior and tests. If the user chooses one larger metadata feature in P3, P3 owns it. | 4.4, 8.5 |
| 17 | `io::isTty`/`termWidth` in P2 | Omitted from P2 `io.obx`; added P5a/P6a. | 5.5 |
| 18 | `OBO_VERSION` bump invalidates syms | Each compiler feature bumps `OBO_VERSION`; rebuild tools after the bump; never `--check` against pre-bump sym dirs; documented. | 6.9, 10.5 |
| 19 | Runtime/binary growth unverified | Measure `runtime_text.bin`, `runtime.elf`, tool/eza binary sizes and peaks after each feature; update README/FORK; no gate on exact size, no budget raise. | 6.8, 10.5 |
| 20 | **New:** non-UTF-8 names are lossy in eza | 0.23.3 `File.name` is `to_string_lossy()`; invalid bytes become U+FFFD in names, target names, sorting and quoting. E0 exact fixtures are ASCII/valid UTF-8; non-UTF-8 is E2 with documented divergence until lossy conversion lands. | 7.12, 8.4 |
| 21 | **New:** long column/type details | Verified from 0.23.3 source: columns perms/size/user/date; regular type char `.`; fifo `\|`; non-regular size `-`; no links/group by default. | 7.8 |
| 22 | **New:** reverse semantics | eza sorts ascending, then reverses the whole vector (ties reversed), then applies group-directories-first as a stable sort. `sort` exposes ascending stable orders plus a separate `reverse`, never a reverse comparator. | 5.3, 7.5 |
| 23 | **New:** `--bytes` locale grouping | eza formats sizes with `locale::Numeric`; English falls back to commas, but the C locale's `thousands_sep` is empty, so `LC_ALL=C` should print plain digits. Probe records it; exact goldens use sizes <1000 except one recorded large-size case. | 7.8, 9.4 |
| 24 | **New:** `-F` no-flag default | `-F` absent → no markers; `-F` present with no value → `auto`; `auto` consumes only `always`, `auto` or `never` as a separate token but accepts `automatic` after `=`. | 5.6, 7.7 |
| 25 | **New:** `--width` makes "auto" behave as TTY | `TerminalWidth::Set` makes `actual_terminal_width()` Some, so `--width` switches non-TTY eza to grid and enables auto colour/classify/icons. E0 rejects `--width`; E2 accounts for it. | 7.11, 8.3 |
| 26 | **New:** operand display includes parent path | Input operands are displayed as parent prefix + name (`/tmp/f`, `./f`), not just the basename; directory listings display names only. `-D`/`-f` are not applied to operand files. | 7.2, 7.4 |
| 27 | **New:** `mutate_io.py` has its own prelude path | **Already migrated on merged main:** `mutate_io.py` compiles every case as a module root with `-I <libdir>`/`--sym-dir` and has no prelude injection; `mutate_port.py` has no prelude comment. P2 only re-anchors the io/library mutant patterns and keeps the count. | 10.3 |

### 1.2a v2 review non-blocking corrections folded in

| Item | v4 rule | Section |
|---|---|---|
| `EZA_STRICT`/`EXA_STRICT`, debug/config env | Add `EZA_STRICT`, `EXA_STRICT`, `EZA_DEBUG`, `EXA_DEBUG`, `EZA_CONFIG_DIR`, `EZA_ICON_SPACING`, `EXA_ICON_SPACING`, `EZA_OVERRIDE_GIT`, `EXA_OVERRIDE_GIT` to the pinned-unset set. Under strict mode eza treats `-aaa`/`-A -A` as duplicate errors; E0 ignores strict mode, so unset it and document the divergence. | 9.5 |
| `--icons` bare semantics | E0 accepts bare `--icons`, `--icons=auto`, `--icons=automatic` and `--icons=never` as an explicit no-icons mode (E0 is non-TTY); rejects `--icons=always`. `--icons automatic` leaves `automatic` as an operand (consume list is `always`, `auto`, `never`). | 5.6, 7.7, 7.11 |
| `paths::base`/`dir` contract | Remove `base`, `dir`, `stem` and `clean` from the required stdlib API: they were unused and shell/GNU-shaped, not Rust `Path`-shaped. Keep `join`, `lastComponent`, `parentOf` (Rust `Path::components` lexical shape), `ext` (Rust `Path::extension` shape, tested but not used by eza), `isAbsolute`, `hasTrailingSlash`, `isDotName`. | 5.2 |
| `cli::argv` argv[0]/`from` | `argv()` returns `args()` including argv[0]; eza calls `parse(argv,1,specs)`. `parse` checks `0 <= from <= len(args)`; `from=len` yields no tokens. | 5.6 |
| `fromBytes` ownership | `fromBytes(data: []byte)` takes the array **by value**: the call moves it to the callee, `string(data)` copies the bytes, and the callee's array is freed at callee scope exit; the caller must not use the array after the call. No zero-copy view. | 5.1 |
| `DT_UNKNOWN` behaviour | Match eza's cached-`DirEntry::file_type()` path: use `d_type` when known; if unknown, eza's `File::filetype()` lazily falls back to `symlink_metadata` (lstat), so `fs`/eza do the same for type, filter, classify and group. P0 probes a DT_UNKNOWN filesystem; normal fixtures have known types. | 5.9, 7.5 |
| Optional-flag error wording | Optional flags (`--classify=foo`, `--color=foo`, `--icons=foo`) report `Option --classify (-F) has no "foo" setting` **without** `(choices: ...)`. Required flags (`--sort=foo`, `--time-style=foo`) include the choices list. `-Ffoo`/`-Fl`/`-F=` are `Flag -F cannot take a value`. Equals splitting is `split_on_equals` for short and long tokens (non-empty name before the `=`, at least one byte after it): `--classify=`/`--sort=`/`--all=` are `Unknown argument ...=`; `--classify==` is a `"="` bad value; `-s=` takes `"="` as the required value (bad value); `-I=` takes `"="`; `-a=` is `Unknown argument -=`; `--=x` is `Unknown argument --=x`. | 5.6, 7.10 |
| `-aa` dot extension/path | `.`'s `File.ext` key is **`File::ext(dir.path)`**: the extension of the operand path's final normal component, `None` when `Path(operand).file_name()` is `None` (operands `.`, `..`, `/`, and any path ending in `..`). Concretely `None` for a dotless operand `d`, `Some("x")` for `dx.x`, `Some("")` for `dx.` (ASCII-lowercased after the last dot), and `None` for `.`, `..` or `/`. Do not implement the shorthand `ezaExt(lastComponent(operand))` literally (`lastComponent(".")` is `"."`, whose `ezaExt` would be `Some("")`). Name-only `-aa` is committed-golden-safe for name/type sorts and for extension with a relative fixture-owned operand **only when every listed pair is comparator-distinct** (default/name and lowercase extension `natcmpFold`; `Name`, `Ext`/`Extension` and type `natcmp`; case-only pairs tie only under fold, whitespace-equivalent pairs under both); **size/modified are live/shape-only**; `-aa -l` is excluded/live. | 7.3, 7.5, 9.4 |
| Operand display normalization | Test `a//b`, `a/./b`, `dir/`, `/`, `.`, `..`, `./f`, `/tmp/f`; eza's `Path` components normalize repeated `.`/`//`/trailing slash. Committed goldens use relative fixture-owned operands only. | 7.2 |
| Permission/broken-pipe status probes | Probe the process status and stderr for directory permission denial, write errors and broken pipes; source prints 13/continues and restores `SIGPIPE`, so do not assume the man page's 13 or a 0 broken-pipe status. | 7.10 |
| Host-golden manifests | Every committed golden records a manifest (reference hash, euid, uid digit width, umask, filesystem, locale, cwd/parent mode+mtime if any). Numeric-user and `-aa -l` cases are live/manifest only; placeholders are repadded. | 9.4 |

### 1.3 Decisions resolved

| Decision | Resolution |
|---|---|
| Module name `args` vs `cli` | **`cli`**. `args` is a universe builtin; the alias check rejects it. No user decision needed. |
| Restricted `-l` | Keep, but only after P0 pins the 0.23.3 shape; require `--bytes --time-style=long-iso` and at least one of `-n`/`--no-user` (both may be given; `--no-user` wins); plain `eza -l` is an own usage error. The user must accept divergence from plain `eza -l`. |
| User/group names, human sizes, local time | Out of E0/E1. Numeric user or `--no-user`, exact bytes, UTC `long-iso`; documented. |
| Colour scope | E1: default palette + `NO_COLOR`; explicit `--color` overrides `NO_COLOR` (verified in source); `LS_COLORS`/`EZA_COLORS` subset later; long-column colour out until probed. |
| Eza reference | Pin 0.23.3 release asset + sha256, install in CI; top external decision. Refusal downgrades to goldens and is flagged. |
| Eza location | **Resolved: `obo/cmd/`** for all four tools; explicit relative stdlib imports; sort tool no `sort` import. Merged main still has `programs/`; P2 performs the move. The user decision is the one-time docs/tests churn, not the location (§12.3 item 3). |
| `--time-style` with a non-long view | With `-l` effective, only `long-iso` is accepted. With a non-long effective view (`-1`, default), `long-iso` and `default` are accepted and ignored (there is no date column); other values are own usage errors. eza accepts and ignores any style there; the divergence is documented. |
| Metadata granularity | E0 only slots 33–36. P5a adds 37/38, P6a adds 39. `currentDir` dropped. |
| Tree/grid/UTF-8 order | E0 names+long; E1 colour+tree; E2 grid+UTF-8. |
| Multiple operands / `-R` / `--sort=none` | Out; reject clearly with exit 3 and own goldens. |
| `-H/--links` | Unsupported in E0; E1 may add it as an optional column. Restricted long has no link column. |
| Non-UTF-8 names | E0 exact cases ASCII/valid UTF-8; E2 implements eza's lossy U+FFFD display and UTF-8 width; divergence documented until then. |
| Tool migration | Move `cat`, `wc`, `sort` and `eza` to `obo/cmd/`; update builder/tests/docs. |

---

## 2. Current state and assumptions

### 2.1 What exists on current main `84558da` (verified; size-fixed compiler, tools-modules merged)

- **Compiler.** `obo/src/obo.obo`, one file, 10,448 lines, 382,329 bytes
  (sha256 `44770afa…`); the compiler and promoted seed are the same 421,889-byte
  binary (sha256 `55dab101…`). Single pass, direct static ELF, no AST/IR/linker.
  `OBO_VERSION = "0.2"`, `SYM_FORMAT = 1`, `SYM_ABI = 1`; `--version` prints
  `obo 0.2 sym-format 1 abi 1`. `make` and the fixed point are green, and
  `seed/obo` == `obo` == stage 2 == stage 3. The size-fixes merge (`134e6a5`,
  seed promoted at `84558da`) changed `src/obo.obo` by 24 lines: the runtime-data
  segment's file size is its nonzero prefix (the zero tail is omitted), and the
  epilogue is `leave; ret`; it added `tests/elf_test.py` and five size mutants.
  The runtime is unchanged by that work. The tools-modules merge (`4e733a6`)
  changed no compiler/runtime byte; the Stage 2 module/`.sym` surface is
  unchanged by the size fixes.
- **Modules Stage 1 and 2.** `module Name`, `import "path.obx" [as alias]`,
  `pub`, `alias::name`, `-I`, `--deps`; cycles rejected; live export tables are
  the type/global registry; dependency-first one image. Stage 2 adds text `.sym`
  files, the reader/writer, `--check`, `--sym-dir`, `--emit-sym`, `--version`,
  `--reemit-sym`, staleness/version errors and root-emission rules.
- **Import resolution.** Resolution is the importer's directory first, then each
  `-I` directory in order; an absolute path is used as written; a file already
  on the import stack is a cycle; an alias may not be a universe name such as
  `args` (`obo/LANGUAGE.md` section 6). A plain name resolves to a same-named
  file beside the importer even when `-I` has another copy; explicit relative
  paths are therefore used by tools.
- **Results and try.** One owned `Result[T]` cell; shared `err` variant
  (`message: string`, `code: int`); `ok`/`err`, consuming and non-consuming
  `match`; `try` outside larger expressions (see §2.2); `?string` is not a
  type.
- **Incremental I/O.** `openFile(path,mode)`, `readFd(fd,max)`,
  `writeFd(fd,data)`, `closeFile(fd)`, `stdin/stdout/stderr`, `args()`,
  `readFile(path)`, `writeFile(path,data,executable)`. FDs are ints; an
  `OBO_CHECK=1` 1024-byte fd table reports leaks (exit 8) and double
  close/use-after-close (exit 9). Runtime slots 0–32. `rtCall` refuses more than
  four arguments. `resultCall3`/`resultCall4` exist; `resultCall2` does not.
  `sys3` exists; `sys4` does not.
- **Runtime.** `obo/runtime/runtime.c`, `entry.S`, `rt.ld`. Freestanding
  x86-64 Linux, no libc, no GC; explicit `rt_free`; strings are
  reference-counted; `rt_init` scans `OBX_GC`, `OBO_CHECK`, `OBO_STATS`,
  `OBO_IO_FAULT` but exposes no environment values to programs.
- **Stdlib modules (merged).** `obo/lib/strings.obx` is `module strings` with
  every declaration `pub`: `isSpace`, `isBlank`, `isPrint`, `isWordByte`,
  `cmpBytes`, `errText`, `digits`, `appendPadded`. `obo/lib/io.obx` is `module
  io` with `Reader`, `Writer`, `clampChunk`, `newReader`, `newWriter`, `fill`,
  `takeChunk`, `readLine`, `readByte`, `readN`, `eof`, `flush`, `write`,
  `writeBytes`, `pump`, `count`, `writePadded`. Both are dependency-free;
  `writePadded` keeps its own digit loop. There is no `lib/str.obo`,
  `lib/io.obo`, `lib/fmt.obx`, `lib/errors.obx` or `lib/fs.obx` on main.
- **Tools (merged).** `obo/programs/{cat,wc,sort}.obx` are module roots
  importing `../lib/io.obx` as `io` and `../lib/strings.obx` as `strings`, and
  every library name is qualified. cat uses only `strings::appendPadded` and
  `strings::errText` (not `isSpace`/`isPrint`); wc uses
  `strings::isSpace`/`isWordByte`/`digits`, `io::writePadded` and
  `strings::errText`; sort uses `strings::isBlank`/`cmpBytes` and
  `strings::errText`. There is no `cmd/`, no `fmt` import and no `errors`
  import yet.
- **Builder (merged).** `obo/tools/build_tools.py` compiles each root directly
  (no concatenation, no generated header, no line map) with `--rt` and a
  private `<out>/sym` created by `_symdir` and passed to the probe and every
  compile. `_map_error` shortens absolute paths under `obo/` to repo-relative
  names in compile diagnostics; it is **not** a generated-line mapper and is
  kept. `build(names, out, obo, rt)` is the single entry point
  (`TOOLS=('cat','wc','sort')`, `LIB_MODULES=('io.obx','strings.obx')`); there
  is no `build_modules` and no global `SYMDIR`. `Makefile` has
  `tools`/`tools-check`/`tools-memory`; no `eza` target.
- **Tests/harness (merged).** `tests/tools.py`: 199 checks (191 matrix + 8
  serial) + 2 memory; `tests/io_test.py` (`--rt --programs --tools`; `case_lib`
  passes `-I <LIBDIR>` with `LIBDIR = <obo>/lib` (an absolute path, so it is
  cwd-independent) and a private sym-dir, and `write_padded` runs);
  `tests/run_fork.py` 1,130 (including `image/runtime_data_segment`);
  `tests/elf_test.py` (program headers, trimmed runtime-data segment, synthetic
  zero-gap/all-zero images, and a normal-program compile-and-run);
  `tests/port_compare.py` 1,147/1,147 on the current
  tree and 1,025/1,025 with `OBO_REF=seed/obo`; `tests/mutate_fork.py` 527/549
  caught + 22 equivalent (the five `SIZE_MUTANTS` are four caught and one
  equivalent); `tests/mutate_port.py`
  495/495; `tests/mutate_io.py` 27 (20 runtime + 7 library) with the scratch
  module tree; `tests/port_test.py` 486/486; `tests/sym_test.py` 27/27;
  `tests/sym_fuzz.py` 200/200; `tests/doc_check.py` 45 examples;
  `tests/homes_check.py`; `tests/homes_edge.py` 1,028 statements / 119 refused
  by both / 0 differ; `bench/bench.py` runs.
- **Memory.** The size-fixed port compiling itself peaks at 1,636,176 bytes live
  and allocates 35,076,608 on a 382,329-byte source (current-tree gate); budget
  2,000,000, spare 363,824; self-compile wall time 0.11 s (median of 5). The
  `port_compare.py` comment still says 954,896 — stale; fix in docs.
- **Tool binaries.** cat 36,865; wc 40,961; sort 45,057 bytes (static, runtime
  included); a minimal `module mini; fn main() {}` executable is 28,673 bytes.
  The pre-size-fix sizes (cat/wc 108,256, sort 112,352; minimal 95,968) are
  historical. The module build produces the same sizes but not the same bytes
  as the old concatenated build; behavior is the invariant.
- **What P2 still adds.** `lib/fmt.obx`, `lib/errors.obx` (`text`),
  `io::writeByte`, the tool migration to
  `strings::cmp`/`errors::text`/`fmt::*`, removal of the four `strings`
  compatibility functions and `io::writePadded`, and the move to `cmd/`. Every
  other "P2 migration" item in v3 is already implemented on merged main and is
  recorded as such below.

### 2.2 Language constraints that bind the design

- No GC. Every record/array/Result is owned; strings are reference-counted.
  A returned owned value passes ownership; `&`/`mut &` borrow.
- No generics, function values, lambdas, methods or interfaces. Sorting is
  keyed and concrete; no comparator callbacks.
- No floats. Sizes, times and widths are integers.
- `Result[T]` is the error convention; `Result[void]` does not exist. `try`
  may appear as a `let`/`var` initializer, an assignment source, a statement or
  a tail expression in a function returning `Result`; it may not be nested
  inside a larger expression (no `f(try g())` or `try f() + 1`), and it is not
  used in `main`. Existing `lib/io.obx` uses `let got = try readFd(...)`.
- Linux x86-64 ABI only. `struct stat`, `linux_dirent64`, ioctl numbers and
  syscall numbers are the x86-64 Linux ABI.
- Single pass, no AST. Metadata is exposed as builtins; no runtime reflection.
- Declare-before-use within a module. Stdlib modules are written
  **callee-first**, types before functions that mention them. No forward
  headings are needed in the stdlib; add one only if a cycle is unavoidable,
  and document it.

### 2.3 Stage 2 surface on main (verified at `84558da`; size-fixed compiler)

The merged CLI and `.sym` rules are the build surface; this design targets them,
not the v2 branch assumptions.

```
obo SOURCE [-o OUT] [--rt DIR] [-I DIR]... [--sym-dir DIR]
           [--check | --deps | --emit-sym] [--version]
           [--reemit-sym FILE]        # testing hook
```

- **Emission.** Every module with a `module Name` header writes `Name.sym` into
  `--sym-dir`. Dependencies always write theirs. A build with at least one
  import writes the root's `.sym` too when the root has a header; a single-file
  build writes none unless `--emit-sym`. `--check` writes the checked module's
  `.sym` and no executable. `--emit-sym` is a build flag (writes the executable,
  accepts `-o`) and requires a root header. `--check`, `--deps` and `--emit-sym`
  are mutually exclusive; `-o` is rejected with `--check`/`--deps`:
  `obxc: -o cannot be used with --check or --deps` (exit 2).
- **Sym-dir.** `--sym-dir DIR` must already exist and be writable; the compiler
  does not `mkdir`. Missing/unwritable:
  `obxc: symbol-file directory '<dir>' does not exist or is not writable`
  (exit 2). The default is the root source's directory, which would pollute
  `obo/cmd/`/`obo/lib/`, so every build script/test passes a private/ignored
  sym-dir. The namespace is flat by module name; concurrent builds must not
  share a sym-dir (writes truncate in place, no atomic rename).
- **Check mode.** `--check SOURCE` resolves imports against
  `<sym-dir>/<basename>.sym` and never compiles a dependency's source; a present
  dependency source validates only header/basename and `source` hash. A
  source-less dependency is allowed in check mode if its `.sym` is valid; a full
  build requires every source:
  `obxc: cannot read module '<m>' at <paths>; only --check may use a symbol file
  without source` (exit 2). `--check` compiles and checks the checked body on
  every run; it is not a cache. A checked root is exempt from the file-name
  rule and may have a private or exported `main`.
- **`--deps`.** Reads headers/imports only, prints normalized source paths in
  depth-first post-order (dependencies before importers, root last), writes
  nothing, rejects `-o`, and needs no sym-dir.
- **Versions.** `--version` prints `obo 0.2 sym-format 1 abi 1`. A `.sym` carries
  exact `obosym 1`, `abi 1`, `compiler "0.2"`, and a source/interface hash.
  Bump `OBO_VERSION` with a compiler feature; `obosym` on grammar/meaning;
  `abi` on layout/calling-convention/descriptor/effect changes. A mismatch is a
  per-line `; rebuild it` error, exit 2.
- **Errors.** Unknown options exit 2; a missing option value says it needs a
  value; source errors exit 1 with `path:line:col`; symbol-file/version errors
  exit 2 with `obxc: ...`. Stage 2 tests and `doc_check` pass private sym-dirs.
- **Build examples (target; run from the `obo/` directory, as the Makefile
  does).**
  ```
  cd obo
  ./obo cmd/cat.obx -o build/tools/cat --rt . --sym-dir build/tools/sym
  ./obo cmd/eza.obx -o build/eza/eza --rt . --sym-dir build/eza/sym
  ./obo --check lib/strings.obx --sym-dir <private>
  ```
  From the repository root the same commands are
  `obo/obo obo/cmd/cat.obx -o obo/build/tools/cat --rt obo ...`; use one cwd
  consistently. The merged builder already invokes the compiler with absolute
  paths, so its behavior does not depend on cwd.
  Headerless `cat`/`wc`/`sort` roots emit no root `.sym`; headed `eza` emits
  `eza.sym`. All imports are explicit relative paths, so no `-I` is needed for
  tool builds. Test roots that import `"io.obx"` from outside `lib/` pass
  `-I <libdir>` and a private sym-dir.

---

## 3. The pinned eza reference (P0)

### 3.1 Pin and acquisition (verified asset; binary run only by the v4 review)

- Version: **eza v0.23.3**, tagged upstream release, published 2025-09-14.
- Asset: `eza_x86_64-unknown-linux-gnu.tar.gz` from
  `https://github.com/eza-community/eza/releases/download/v0.23.3/eza_x86_64-unknown-linux-gnu.tar.gz`.
- Tarball sha256 (verified by download; matches the GitHub release asset
  digest): `02e818f7b06f2c637b25761731104f1e9d6ea8f6e5924ab05eae69db4ed072a5`.
- Extracted binary sha256 (verified by extracting and hashing; **not run by
  this design pass** — the independent v4 review, v5 re-review and v6 review
  ran it in throw-away probes, §3.3a):
  `10457dde35808517d09bcd3bfb6bc386000d06c2126e34b9071e09470a8f7cd8`.
- The binary is a dynamically linked x86-64 GNU/Linux PIE (glibc); CI is
  GNU/Linux x86-64.
- The pinned release binary's `--version` is exactly
  `eza - A modern, maintained replacement for ls`, `v0.23.3 [+git]`,
  `https://github.com/eza-community/eza`. The `[+git]` suffix is part of the
  release string (observed by the independent reviews; `build.rs` appends
  nonstandard features in release builds). `tests/eza/VERSION` and `--require-reference`
  match that exact line; do not normalize it to `v0.23.3`.
- Do **not** pin Ubuntu 24.04's `eza 0.18.2-1` candidate: it predates
  `--treat-dirs-as-files` (added in 0.22.1), its man page has no exit 13, and
  its long/sort/quoting behaviour differs from the reviewed 0.23.3 source.
  B8 would fail. A different pin requires re-deriving the whole flag/column
  table and re-recording goldens.

P0 tasks:

1. Download the pinned tarball outside the repo (e.g. `/tmp/eza-ref`), verify
   the tarball sha256, extract, verify the binary sha256, and run
   `eza --version` to record the exact three-line release string.
2. Write `obo/tests/eza/VERSION` containing the exact `eza --version` output,
   the tarball sha256, the binary sha256, and the acquisition URL.
3. Write `obo/tests/eza/REFERENCE.md`: the exact flag table, help output,
   defaults, long columns/widths, quoting, sort probes, colour codes, tree
   glyphs, exit statuses and stderr shapes observed. This file is data, not
   design prose.
4. Install the same asset in CI with digest verification (section 10.1's
   command), outside the repository.
5. Record the initial golden matrix (section 9).

`tests/eza.py --require-reference` fails with exit 2 (setup error) unless the
installed reference's version output and binary sha256 match `VERSION` exactly
and the required flags probe succeeds. A missing or different reference is a
setup failure, never a silent skip. Hosts without eza may run committed
goldens; the release review requires the live differential.

### 3.2 If the user will not provide a reference

Then E0 cannot meet the roadmap's differential stop condition. The design would
degrade to own goldens and a shape comparison; that is a recording of our own
choices, not differential testing. Flag it explicitly as a violated stop
condition and do not describe the result as eza parity. This is the top
external decision (section 12).

### 3.3 Eza 0.23.3 source facts (observed rows marked)

Most rows are read from the v0.23.3 source tarball (`codeload.github.com`,
tag `v0.23.3`) and the `natord-plus-plus 2.0.0` / `locale 0.2.2` crates. Rows
that mention an observed value (`[+git]`, a target display, a parser message)
come from the independent v4, v5 and v6 review throw-away probes recorded in
§3.3a. All remain P0 re-records, not committed bytes.

| Area | Fact (source-verified unless it says observed) | Source |
|---|---|---|
| Default view on non-TTY | With no view flag and stdout not a terminal, eza selects `Mode::Lines` (one entry per line). With `--width=N`, `TerminalWidth::Set` makes `actual_terminal_width()` `Some`, so eza treats it as terminal-ish and may grid. | `src/options/view.rs`: `Mode::deduce`, `TerminalWidth::deduce`; `src/output/mod.rs`: `actual_terminal_width` |
| Lines/default symlink targets | `lines.rs` calls `.with_link_paths()`; default/`-1` always render `name -> target` (not just the name). The target parent uses the outer quote style, the target name uses `QuoteSpaces`; broken targets are escaped with the outer style; error targets show no arrow. | `src/output/lines.rs:31-42`; `src/output/file_name.rs` `FullLinkPaths` |
| Classify marker placement | Lines/default/`-1`/`-d` and long/tree show the target and append the marker to the **target** (`link -> target/`); grid omits target paths and appends the marker to the **link** (`link@`). Broken/error targets get no marker. | `src/output/file_name.rs`; `src/output/lines.rs`; `src/output/grid.rs` |
| `-a` count | `matches.count(ALL)`; c=0,!A `JustFiles`; c=1 or (c=0,A) `Dotfiles`; c≥2 `DotfilesAndDots` (tree + c≥2 is an error). `--almost-all` is a boolean, not a count. | `src/options/filter.rs`: `DotFilter::deduce` |
| Dot synthesis | Rust `read_dir` never yields `.`/`..`; eza synthesizes them first when `DotfilesAndDots`, with names `.`/`..`, paths `dir.path` and `dir.path.join("..")`, and `is_all_all=true`. Real entries beginning `.` are filtered unless dotfiles. **`File::new_aa` leaves metadata/filetype lazy:** `--sort=size`/`--sort=modified` read the listed directory's and host parent's metadata, so those name-only orders are host-dependent. Name/type/extension orders are host-independent only when every listed pair is comparator-distinct; equal comparator keys preserve `readdir` order (§7.5). | `src/fs/dir.rs`: `Files::next`, `next_visible_file`; `src/fs/file.rs`: `new_aa`, `length`, `modified_time` |
| `-d` spelling | Canonical `-d, --treat-dirs-as-files`; `--list-dirs` is also defined and `DirAction::deduce` checks either. No other alias. | `src/options/flags.rs:43-44`; `src/options/dir_action.rs:22` |
| `-F` values | `TakesValue::Optional(WHEN, "auto")`, `WHEN=["always","auto","never"]`. Parser consumes a separate next token only if it is in WHEN; `--classify=automatic` is accepted by deduce, `--classify automatic` leaves `automatic` as an operand. No `-F` flag at all means `JustFilenames` (no markers). | `src/options/flags.rs:21,31`; `src/options/parser.rs:207-221`; `src/options/file_name.rs:40-55` |
| `--color`/`--icons` | Same optional `WHEN` machinery; both long-only. `--colour` is an alias. `UseColours::deduce`: absent flag defaults to `Never` if `NO_COLOR` is present in the environment, else `Automatic`; an explicit flag overrides `NO_COLOR` (`--color=auto` re-enables automatic even with `NO_COLOR`). | `src/options/flags.rs:29-30,67`; `src/options/theme.rs:63-86` |
| Icons default/bare | `ShowIcons::deduce`: no `--icons` and no `EZA_ICONS_AUTO` → `Never`; bare `--icons` or `--icons=auto`/`automatic` → automatic (non-TTY means no icons); `--icons=never` → Never; `--icons=always` → Always. | `src/options/file_name.rs:57-105`; `src/options/flags.rs:67` |
| Strict mode env | `EZA_STRICT`/`EXA_STRICT` non-empty selects `Strictness::ComplainAboutRedundantArguments`; absent/empty selects use-last. Strict mode makes duplicate/redundant flags (`-aaa`, `-A -A`, repeated `--sort`) errors. E0 pins these unset and implements use-last only. | `src/options/mod.rs:143-155`; `src/options/vars.rs:40-41` |
| Debug/config env | `EZA_DEBUG`/`EXA_DEBUG` configure logging; `EZA_CONFIG_DIR` selects a theme config; `EZA_ICON_SPACING`/`EXA_ICON_SPACING` and `EZA_OVERRIDE_GIT`/`EXA_OVERRIDE_GIT` affect out-of-scope features. Pin all unset. | `src/main.rs:60`; `src/options/theme.rs:38`; `src/options/file_name.rs:82`; `src/options/view.rs:274` |
| Sort spellings | `name`/`filename`→Name(AaBbCc); `Name`/`Filename`→Name(ABCabc); `.name`/`.filename`/`.Name`/`.Filename`; `size`/`filesize`; `ext`/`extension`→Extension(AaBbCc); `Ext`/`Extension`→Extension(ABCabc); `date`/`time`/`mod`/`modified`/`new`/`newest`→ModifiedDate; `age`/`old`/`oldest`→ModifiedAge; `ch`/`changed`; `acc`/`accessed`; `cr`/`created`; `inode`; `type`; `none`. Default is `Name(AaBbCc)`. | `src/options/filter.rs:50-98` |
| Name comparator | `Name(ABCabc)` uses `natord::compare` (case-sensitive); `Name(AaBbCc)` uses `natord::compare_ignore_case`. The dependency is `natord-plus-plus = "2.0"`; the algorithm is Martin Pool-style, whitespace-skipping, natural numeric runs with leading-zero handling. Neither comparator is injective on distinct names: `compare("A","a") = Less` but `compare_ignore_case("A","a") = Equal`; `compare_ignore_case("README","readme") = Equal`; `compare("pic4","pic 4") = Equal`; `compare("a"," a") = Equal`. eza sorts with stable `sort_by`, so Equal pairs keep `readdir` order. | `src/fs/filter.rs:136-160,295-310`; `Cargo.toml`; natord-plus-plus 2.0.0 `lib.rs` |
| Extension comparator | Primary `a.ext.cmp(&b.ext)` on `Option<String>` (None first, then byte order); tie fallback `natord::compare` for `Extension(ABCabc)`, `natord::compare_ignore_case` for lowercase. `File.ext` is the substring after the **last dot** (so `.git` has ext `git`), ASCII-lowercased. Synthesized dots take their ext from the constructed path passed to `File::new_aa`: `.` gets `dir.path` (`new_aa_current`), so its key is **`File::ext(dir.path)`** — the extension of the path's final normal component, or `None` when `Path::file_name()` is `None`. Concretely `None` for operands `.`, `..` and `/`, `None` for a dotless operand `d`, `Some("x")` for `dx.x`, `Some("")` for `dx.`; `..` gets `dir.path.join("..")`, whose `file_name()` is `None`, so it has no extension. Do not implement `ezaExt(lastComponent(operand))` literally: `lastComponent(".")` is `"."`, whose `ezaExt` is `Some("")`, not `None`. | `src/fs/file.rs:150-200,236-248`; `src/fs/dir.rs`: `new_aa_current`; `src/fs/filter.rs:284-292` |
| Type comparator | `Type` order: Directory, File, Link, Pipe, Socket, CharDevice, BlockDevice, Special. Compare type rank, tie fallback **case-sensitive** `natord::compare` (even for lowercase `type`). | `src/fs/fields.rs:48-60`; `src/fs/filter.rs:282-287` |
| Type source / `DT_UNKNOWN` | `File::from_args` caches `DirEntry::file_type().ok()`. If it is `None` (unknown d_type/error), `File::filetype()` lazily falls back to `metadata()` = `symlink_metadata` (lstat), so type/filter/classify/group use lstat; if lstat fails, the type is unknown/special. P0 probes a DT_UNKNOWN filesystem. | `src/fs/file.rs` `from_args`, `filetype`, `metadata`; `src/fs/dir.rs` |
| Size/date comparator | Size uses `File::length()` = `metadata.len()` (lstat st_size) for every type, or recursive size with `--total-size`; no name tie-break (equal keys keep readdir order). Date uses `Option<NaiveDateTime>` (None first), full second+nanosecond precision, no name tie-break (equal keys keep readdir order). | `src/fs/filter.rs:278-282`; `src/fs/file.rs:690-700` |
| Reverse | `sort_by(comparator)` (stable), then if `-r`, `files.reverse()` (reverses ties too), then `--group-directories-first` as a second stable sort on `points_to_directory()`; group order is not reversed by `-r`. | `src/fs/filter.rs:136-160` |
| Default sort | `Name(AaBbCc)` case-folded natural. | `src/options/filter.rs:55-58` |
| Quoting | `escape`: quote if the name contains a space or an apostrophe and quote style is not `NoQuotes`; opening/closing `"` if the name contains `'`, else `'`; characters `<0x20` or `0x7f` are escaped with Rust `char::escape_default` (`\t`, `\r`, `\n`, other `\u{...}`); embedded quotes/backslashes are not escaped; printable non-ASCII passes through. Symlink target names always use `QuoteSpaces`, regardless of the outer style. | `src/output/escape.rs`; `src/output/file_name.rs:200-340` |
| Long columns | `Columns::collect` order: Inode, Octal, Permissions, HardLinks, FileSize, Blocksize, User, Group, FileFlags, SecurityContext, timestamps, Git. Defaults for `-l`: permissions, filesize, user on; `--no-*` turn them off. Links only with `-H`; group only with `-g`; inode only with `-i`; blocks only with `-S`. Time defaults to modified. | `src/output/table.rs:60-175`; `src/options/view.rs:240-280` |
| Long alignment | Widths are per-column maxima; each cell is followed by one space; FileSize/HardLinks/Inode/Blocksize/GitStatus are right-aligned, everything else (including User and Timestamp) left-aligned; the name is appended after the table. | `src/output/table.rs:180-205,602-620` |
| Long size | `File::size()`: regular file → `metadata.len()`; directory/symlink/fifo/socket/other → `Size::None` rendered as `-`; char/block device → `major,minor`. `--bytes` uses `SizeFormat::JustBytes` and `locale::Numeric::format_int`. | `src/fs/file.rs:593-630`; `src/output/render/size.rs:20-55`; `src/output/cell.rs:66-78` |
| Long user | `UserFormat::Numeric` with `-n` prints `uid.to_string()`; otherwise names via `uzers`. The user cell is left-aligned. | `src/output/render/users.rs:15-40`; `src/output/table.rs:188-198` |
| Long date | `long-iso` = `%Y-%m-%d %H:%M`; timestamps are local-time (`TimeFormat::format` receives a `DateTime<FixedOffset>`); a timestamp that cannot be represented (e.g. pre-1970 `duration_since(UNIX_EPOCH)` failure) renders `-`. | `src/output/time.rs:105-115`; `src/output/render/times.rs:18-35`; `src/fs/file.rs:775-790` |
| Permission type chars | eza's first permissions char: regular `.`, directory `d`, symlink `l`, FIFO `\|`, socket `s`, block `b`, char `c`, special `?`; setuid/setgid/sticky render `s`/`S` and `t`/`T`; xattrs append `@` if enabled. No ACL marker. | `src/output/render/filetype.rs:10-25`; `src/output/render/permissions.rs:15-55` |
| Classify markers | Regular file with **user-execute** bit `*`, directory `/`, FIFO `\|`, symlink `@`, socket `=`; nothing for devices. In lines/default and long/tree where a target is shown, the marker is appended to the **target**, not the link; grid puts it on the link; broken/error targets get no marker. | `src/output/file_name.rs:210-330,371-390`; `src/fs/file.rs:337-355`; `src/output/lines.rs` |
| Operand symlink | `points_to_directory()` follows a symlink; an operand symlink to a directory is listed as a directory unless `-d`. Entry symlinks are not followed by `is_directory()` and are not descended in tree unless `--follow-symlinks`. | `src/fs/file.rs:296-315`; `src/main.rs:265-290`; `src/fs/filter.rs:110-135` |
| Symlink target metadata | `File::link_target` reads the link, then `reorient_target_path`: an absolute target is used as written; otherwise `parent_dir.join(target)` for a directory entry, else `self.path.parent().join(target)`. It then calls `std::fs::metadata` (follow), not `symlink_metadata`. A metadata error → `FileTarget::Broken(raw)`, no marker; a readlink error → `FileTarget::Err`, no arrow. `Broken` display escapes the whole raw target with the outer style. | `src/fs/file.rs:424-490`; `src/output/file_name.rs:238-320` |
| Parent-prefix quoting | `add_parent_bits` escapes `parent.to_string_lossy()` **once** with the outer quote style and appends `/`, with one branch: when the parent has exactly one component and it is the root, it emits a single `/` and does **not** append another separator. The target's parent uses the outer style while the target name uses `QuoteSpaces`. A target `/tmp` therefore displays `/tmp` (not `//tmp`); a target `/` displays `/`, and `-F=always` appends the directory marker to give `//`. | `src/output/file_name.rs:343-366` |
| Operand display | Input files have `parent_dir=None`; `FileName::paint` prepends the path's parent (lossy, quoted with the outer style) and a `/` before the name. Directory entries have a parent and display only their name. | `src/output/file_name.rs:238-265,343-366` |
| `-D`/`-f` | `-D` uses `is_directory()` (real directory only; symlink-to-dir excluded); `-f` uses `is_file()` (regular file only; symlinks/FIFOs/sockets excluded). Both together fall through to no filtering. They are applied to directory children, **not** to argument files. | `src/fs/filter.rs:88-135,160-165`; `src/main.rs:290-296` |
| Exit statuses | Constants: success 0, runtime error 1, options error 3, permission denied 13. `main` also sets status **2** when an input path's metadata lookup fails, printing `{file_path:?}: {e}` with no `eza:` prefix. Option errors print `eza: {error}` and exit 3. Directory permission-denied prints `Permission denied: <path> - code: 13`, records the directory and continues; the shown code does not assign the process status 13 in `print_dirs`. A broken pipe exits 0 only if the error is observed; eza restores `SIGPIPE` to default first. | `src/main.rs:100-145,250-330,405-415,550-560` |
| Version output | Release builds print `eza - A modern, maintained replacement for ls`, `v0.23.3 [+git]`, `https://github.com/eza-community/eza`, then a newline. The `[+git]` suffix is part of the pinned release string (observed on the pinned asset). | `build.rs`; `src/options/version.rs` |
| `--bytes` grouping | `SizeFormat::JustBytes` formats with `locale::Numeric` (thousands separators every 3 digits). English fallback is `,`; the C locale's numeric `thousands_sep` is empty, so `LC_ALL=C` should print plain digits. The harness pins `LC_ALL=C`; P0 records a 7-digit size to confirm. | `src/output/render/size.rs:39-55`; locale 0.2.2 `lib.rs:140-181` |
| Non-UTF-8 names | `File::filename` uses `to_string_lossy()`; invalid bytes become U+FFFD in names, targets, sorting and quoting. Paths given on the command line are `OsStr` bytes for lookup, but display is lossy. | `src/fs/file.rs:215-230`; `src/options/parser.rs` header |

### 3.3a Independent review observations (v4, v5 and v6 reviews; P0 re-records, not committed goldens)

The independent v4 review, the focused v5 re-review and the v6 review ran the
cached pinned binary in several throw-away probes under `/tmp` and `/dev/shm`
with `LC_ALL=C`, `LANG=C`, `LANGUAGE=C`, `TZ=UTC` and colour/icon/grid
environment unset. The observations below are review evidence for the rules in
this note. P0 repeats them, records exact bytes in `REFERENCE.md`, and commits
only the goldens allowed by §9.4; none of these lines is a committed golden by
itself.

| Probe | Observation |
|---|---|
| `--version` | `eza - A modern, maintained replacement for ls` / `v0.23.3 [+git]` / `https://github.com/eza-community/eza` |
| Tie order, `-aa --sort=name`, files `a` then `A` | tmpfs: `. .. A a`; ext2/3: `. .. a A` (readdir order; `compare_ignore_case("A","a") = Equal`) |
| Tie order, default lines, files `a` then `A` | tmpfs: `A a`; ext2/3: `a A` |
| `-aa --sort=extension d` (dotless operand) | `. .. b sub a.txt` (`.` key `None`) |
| `-aa --sort=extension dx.x` | `.. b a.txt .` (`.` key `Some("x")`) |
| Absolute target `/tmp` | `abstmp -> /tmp`; with `-F=always`: `abstmp -> /tmp/` |
| Absolute target `/` | with `-F=always`: `absroot -> //` (target `/` plus the directory marker) |
| Absolute spaced target `/tar get` (Broken) | `spaceabs -> '/tar get'`; with `-F=always` the same (no marker). It is a broken target, not a valid root-parent case. |
| Operand `.` extension key | `-aa --sort=extension .` puts `.` in the `None` group (observed; the operand path has no `file_name()`). `..`/`/` are the same `File::ext(dir.path)` source rule (no `file_name()`), not separately observed; P0 re-records all three. |
| Spaced parent operand | `x y/sub/f` → `'x y/sub'/f`; `-d x y/sub/link -F=always` → `'x y/sub'/link -> target*` |
| `-d sub/link` with cwd decoy `target` (644) and `sub/target` (755) | `sub/link -> target*` (resolves `sub/target`) |
| Chain `b -> real -> realdir` | `b -> real/` (followed stat) |
| Loop `a -> b -> a`; broken target | no marker |
| Target quoting | `link -> 'a b'/target` (valid target under the spaced parent `a b`); broken `'a b/missing'`; broken `link -> '../tar get'` (no sibling `tar get` in the case directory's parent; no marker even with `-F=always`) |
| Long `-l -n --bytes --time-style=long-iso` | reproduces §7.8's worked example; `--bytes` under `LC_ALL=C` prints `1234567` with no grouping |
| Parser | options after operands (`eza d -a` lists `.h`, `f`); `-lF=always` accepted; `--classify=automatic` and `-F=automatic` accepted; `-F automatic` leaves `automatic` an operand (exit 2); `-Fautomatic`/`-Fl` → `eza: Flag -F cannot take a value`; `--classify=foo` → `eza: Option --classify (-F) has no "foo" setting`; `--sort=default` → choices error; `--classify=` and `--sort=` → `Unknown argument ...=`; `-F=` → `eza: Flag -F cannot take a value`; `-s=` → `Option --sort (-s) has no "=" setting (choices: ...)`; `-I=` accepted with value `=`; `-a=` → `Unknown argument -=`; `--classify==` → `Option --classify (-F) has no "=" setting`; `--all=` → `Unknown argument --all=`; `--=x` → `Unknown argument --=x`; `--foo=bar`/`--foo==` → `Unknown argument --foo`; `--foo=` → `Unknown argument --foo=`; `-Qfoo`/`-Q=foo` → `Unknown argument -Q` |
| natord-plus-plus 2.0.0 equality classes | `compare("A","a") = Less`; `compare_ignore_case("A","a") = Equal`; `compare_ignore_case("README","readme") = Equal`; `compare("pic4","pic 4") = Equal`; `compare("a"," a") = Equal` |

### 3.4 Probe list (not source-verifiable)

P0 must record all of these before E0 implementation:

1. Exact `eza --version` bytes (the release string above), `--help` bytes, and
   the binary's sha256.
2. Default non-TTY output (one entry per line, **with `name -> target` for
   symlinks**) on empty/one/mixed directories.
3. `-a`, `-aa`, `-A`, `-A -a`, `-A -aa`, `--all --all`, cluster spellings;
   synthesized dot order/display, the `.` extension key for a dotless operand
   `d` (`None`), `dx.x` (`Some("x")`), `dx.` (`Some("")`) and the operand
   `.`/`..`/`/` (`File::ext(dir.path)` → `None`), `-aa -l` host data, and the
   name-only `-aa --sort=size`/`--sort=modified` host-dependent order.
4. `-d`/`--treat-dirs-as-files`/`--list-dirs` on file/dir/symlink operands,
   with and without `-l`; operand display normalization (`a//b`, `a/./b`,
   `dir/`, `./f`, `/`, `.`, `..`), a spaced parent (`x y/sub/f`,
   `-d x y/sub/link`), and trailing slashes.
5. Default name order on `A a B b`, `2 10`, `rfc1 rfc2 rfc10`, leading zeros,
   spaces, punctuation, `.hidden`, `-`, `--x`; comparator-tie probes
   `A`/`a`, `README`/`readme`, `pic4`/`pic 4`, `a`/` a` (readdir-order
   sensitive, live only). Committed sort cases use comparator-distinct names.
6. `--sort=Name`, `extension`, `Ext`, `Extension`, `ext`, `size`, `modified`,
   `time`, `type`, `none`, `inode`; error wording for bad values; ties with
   `-r`; `--group-directories-first` with `-r`.
7. Quoting: spaces, apostrophe, both, double quote, backslash, tabs, newlines,
   DEL, ESC, mixed controls, empty-ish names; valid/broken/error symlink
   targets; `--no-quotes` (not E0, but record).
8. Classify: `-F`, `-F=always`, `-Falways`, `-F always`, `--classify=always`,
   `--classify=automatic`, `-F automatic` (the last should leave `automatic`
   as an operand); markers for file/dir/executable/symlink/fifo/socket;
   **lines/default/`-1`/`-d` target markers** and long target markers.
9. Restricted long on every fixture type/size/mode/mtime, with `-n` and with
   `--no-user`; exact column widths, separators, permission chars, `-` sizes,
   date rendering, pre-1970 mtimes, 7-digit size grouping, uid widths for
   root/non-root; `-aa -l` and absolute/`-d ..`/`-d /` host data.
10. Exit/stderr: missing operand, file-as-directory, permission denied (non-root),
    unknown short/long option (including `--foo=bar`, `--foo==`, `--foo=`,
    `--=x`, `-Qfoo`, `-Q=foo`, recording the reported name in each), missing
    required value, forbidden value, bad value, `-l` without our required
    flags (reference does not have this restriction; own golden), write error
    `/dev/full`, broken pipe.
11. Symlink operands: to file, to dir, dangling, relative/absolute target;
    lines/default and long target display, broken/error targets. Absolute
    targets whose parent is the root: `/tmp` and `/tmp/` (`-F=always`), and
    `/` with and without `-F=always` (`//` with the marker). Also probe a raw
    target `/tmp/` (trailing slash) to pin whether the trailing slash is
    normalized before the marker. Probe the broken absolute spaced target
    `/tar get` separately: a normal test cannot create it, so it must display
    as `spaceabs -> '/tar get'` with no marker even under `-F=always`; it is
    not a root-parent case and does not exercise the parent-prefix branch.
    Probe the broken relative spaced target `../tar get` with **no** sibling
    `tar get` in the case directory's parent: it must display
    `link -> '../tar get'` with no marker even under `-F=always`; do not create
    the sibling.
12. `--icons` bare/`never`/`auto`/`automatic`/`always` and default (non-TTY)
    icons; **bare `--color`/`--colour`**, `--color=auto|automatic|never`;
    `NO_COLOR` with and without `--color`; `--color=always` SGRs (E1);
    `LS_COLORS`/`EZA_COLORS` (E1).
13. `--width` non-TTY grid and auto colour/classify/icons interaction (E2).
14. Tree `-T`/`-L` glyphs, empty dirs, hidden, symlink dirs (E1).
15. Environment variables that affect output: `TIME_STYLE`, `COLUMNS`,
    `EZA_GRID_ROWS`, `EXA_GRID_ROWS`, `EZA_ICONS_AUTO`, `NO_COLOR`,
    `LS_COLORS`, `EZA_COLORS`, `EXA_COLORS`, `TZ`, `EZA_STRICT`, `EXA_STRICT`,
    `EZA_DEBUG`, `EXA_DEBUG`, `EZA_CONFIG_DIR`, `XDG_CONFIG_HOME`,
    `EZA_ICON_SPACING`, `EXA_ICON_SPACING`, `EZA_OVERRIDE_GIT`,
    `EXA_OVERRIDE_GIT`, `EZA_MIN_LUMINANCE`, `EXA_MIN_LUMINANCE`.
16. A `DT_UNKNOWN` filesystem (or a crafted probe) to confirm the cached
    `DirEntry::file_type()`/lazy-lstat fallback for type/filter/classify/group.

Until P0 records these, the design's eza rules are **design targets**, not
observed eza behaviour. Where the probe contradicts a rule, the probe wins and
this note is revised.

---

## 4. Repository layout, import paths and file ownership

### 4.1 Layout and the import rule

```
obo/lib/        stdlib modules (imported; module header; pub API)
  strings.obx   paths.obx   errors.obx   sort.obx
  io.obx        fmt.obx     cli.obx      time.obx
  fs.obx        env.obx
obo/cmd/        tool roots (headerless except eza; not imported)
  cat.obx  wc.obx  sort.obx  eza.obx
```

- `cat`, `wc`, `sort` stay **headerless roots** (`_root`). `eza.obx` declares
  `module eza`; it is a root and is never imported. A root header is allowed by
  the modules design; the build writes a root `.sym` for eza into the private
  sym-dir, which nothing reads. If the user prefers headerless eza, that is
  also legal; the module name is not used by anything else.
- **Every tool imports the stdlib by explicit relative path.** Examples:
  ```obo
  // obo/cmd/cat.obx
  import "../lib/io.obx" as io
  import "../lib/fmt.obx" as fmt
  import "../lib/errors.obx" as errors

  // obo/cmd/wc.obx
  import "../lib/io.obx" as io
  import "../lib/fmt.obx" as fmt
  import "../lib/strings.obx" as strings
  import "../lib/errors.obx" as errors

  // obo/cmd/sort.obx  — no sort import; it uses strings::cmp and its own parser
  import "../lib/io.obx" as io
  import "../lib/strings.obx" as strings
  import "../lib/errors.obx" as errors

  // obo/cmd/eza.obx
  import "../lib/io.obx" as io
  import "../lib/fmt.obx" as fmt
  import "../lib/strings.obx" as strings
  import "../lib/paths.obx" as paths
  import "../lib/sort.obx" as sort
  import "../lib/cli.obx" as cli
  import "../lib/errors.obx" as errors
  import "../lib/time.obx" as time
  import "../lib/fs.obx" as fs
  import "../lib/env.obx" as env        // only from P5a (E1)
  ```
- Stdlib modules import each other by same-directory name (`fmt` imports
  `"io.obx"`, `sort` imports `"strings.obx"`). Those basenames cannot collide
  with a tool file in the same directory.
- Why explicit relative paths: `resolveSource` searches the **importer's
  directory first**. A plain `"sort.obx"` in `obo/cmd/eza.obx` would resolve to
  `obo/cmd/sort.obx` (a headerless tool root) and fail; in `obo/cmd/sort.obx`
  itself it resolves to the file on the import stack and is a cycle. Relative
  `../lib/sort.obx` is unambiguous and is the rule even though the standard
  library is also reachable with `-I lib`. Do not rely on `-I` for tool
  imports.
- No `-I` is required for a tool build. `-I obo/lib` is still passed by the
  test harness for test roots that live outside `obo/cmd/` (section 10.3), and
  it is harmless.
- **The sort tool must not import `sort`.** It does not use
  `sort::orderStrings`; its GNU `strnumcmp` parser, tie rules and in-place
  output stay private until a separate output-proven change. Adding the import
  would fail at `cmd/sort.obx` itself (cycle) and is forbidden.

### 4.2 Compile tests for every import

Add `obo/tests/imports.py`. **P1** covers the stdlib; the tool matrix is added
in **P2**, when `obo/cmd/` exists.

P1 (stdlib only, on current size-fixed main; Stage 2 surface):

1. Compile each stdlib module through a tiny importer root: `main.obx` imports
   the module and calls one exported function/constructs one exported type;
   build with `-I <lib>`, a private sym-dir and `--rt`. This avoids requiring
   `main` in the module itself. For a type-only module, use a value/type use or
   `--check` after its dependencies' syms exist.
2. Temporary collision/decoy trees, with exact diagnostics:
   - `tmp/dir/a.obx` headerless, `tmp/dir/b.obx` imports `"a.obx"` → **not** a
     cycle: `imported module '<path>' needs a 'module Name' header`, exit 1;
   - `tmp/dir/self.obx` importing `"self.obx"` → `import cycle: ... self.obx ->
     self.obx`, exit 1; a two-module mutual import → a cycle listing both paths;
   - a plain `"sort.obx"` from a directory containing a `sort.obx` decoy
     resolves to the **importer-dir** file, not `-I`'s copy; pin the exact
     diagnostic (headerless decoy → needs-header; headed decoy → the basename/
     module-name rule), so the collision class is tested rather than assumed.
3. `sort.obx` importing `"strings.obx"` compiles through the tiny importer;
   this is the stdlib same-directory import (not a tool import).
4. P1 requires the current size-fixed compiler (Stage 2 surface): every build passes
   `--sym-dir <private>`. If a Stage 1 compiler is ever used for a no-import
   case, the helper omits `--sym-dir`; it must not pass it unconditionally.
5. **P1 tool-build check.** Because P1 edits the merged `lib/strings.obx` in
   place, the P1 gate also runs `make tools`, `python3 tests/tools.py
   --require-reference` (199/199) and `python3 tests/tools.py --memory` (2/2)
   plus `python3 tests/io_test.py --tools`. P1 is build-neutral by measurement,
   not by assertion; the four compatibility functions are what make this pass.

P2 (after `cmd/` exists):

1. Compile each tool source (`cat`, `wc`, `sort`, `eza` once P4) into a private
   output directory with a private sym-dir; assert success.
2. Assert statically that `cmd/sort.obx` contains no `import "sort.obx"` and
   that every tool import path is `../lib/...`; assert `cmd/eza.obx` imports
   `../lib/sort.obx`, never plain `"sort.obx"`.
3. Re-run the decoy test against `cmd/` so a future same-named tool file cannot
   silently capture a stdlib import.

### 4.3 `strings.obx` ownership and the P1/P2 merge (B2, rebased)

**Merged main already has `lib/strings.obx`; P1 and P2 edit it in place, they
do not create it, and there are no `.obo` files to delete or rename.**

| merged function | destination | owner |
|---|---|---|
| `isSpace`, `isBlank`, `isPrint`, `isWordByte` | `strings` (unchanged) | P1 keeps |
| `cmpBytes` | `strings::cmp` (P1 adds `cmp`); `cmpBytes` stays as a temporary compatibility function | P1 adds `cmp`, keeps `cmpBytes`; P2 removes `cmpBytes` |
| `errText` | `errors::text` (P2 creates `lib/errors.obx`); `errText` stays temporary until then | P2 adds `text` and removes `errText` |
| `digits`, `appendPadded` | `fmt::digits`, `fmt::appendPadded` | P2 adds `fmt.obx`; P2 removes the `strings` copies |
| `io::writePadded` | `fmt::writePadded` | P2 moves it and removes it from `io.obx` |

Rules:

- **P1 edits `lib/strings.obx` in place.** It adds the §5.1 declarations that
  are not already present (including `cmp`) and **keeps** `cmpBytes`, `errText`,
  `digits`, `appendPadded` (and the four classifiers) so the merged
  `programs/{cat,wc,sort}.obx` keep compiling. The four compatibility functions
  are documented in the source as temporary and are not part of the post-P2
  public contract. P1 does not modify `lib/io.obx` and does not create
  `lib/fmt.obx`/`lib/errors.obx`.
- **P2, in the same change**, adds `lib/fmt.obx`, adds `lib/errors.obx` with
  `text`, adds `io::writeByte`, moves `programs/{cat,wc,sort}.obx` to `cmd/`,
  switches them to `strings::cmp`/`errors::text`/`fmt::digits`/
  `fmt::appendPadded`/`fmt::writePadded`, and removes `cmpBytes`, `errText`,
  `digits`, `appendPadded` from `lib/strings.obx` and `writePadded` from
  `lib/io.obx`.
  P2 edits both files; there is no rename and no replacement of a P1-owned
  path. P2 must leave `strings::cmp` self-contained (P1 may implement the body
  once and make `cmpBytes` a wrapper; after P2 no remaining function calls a
  removed compatibility name).
- The v3 delete/rename steps (`lib/str.obo`, `lib/io.obo`) are **removed from
  the plan** because those files do not exist on merged main; they are not
  no-op instructions to carry forward.
- Merge order: P1 edits `lib/strings.obx` additively and may merge first. P2
  merges after P1; if P1 is not merged, P2 must not start. This is a real
  dependency, not an "atomic" claim.
- No `.obo` compatibility copy and no concatenation path exists on main or is
  reintroduced.

### 4.4 Module graph and staging

```
strings   (leaf)
paths     (leaf)
errors    (leaf; P2)
cli       (leaf)
time      (leaf)
sort      -> strings
io        (leaf; builtins only)
fmt       -> io
fs        (leaf; builtins only)              P3
env       (leaf; getenv builtin)             P5a
```

- P1: edit `strings` in place (add §5.1, keep the four compatibility
  functions), add `paths`, `sort`, `cli`, `time` + tests.
- P2: `errors`, `io`, `fmt` + tool migration.
- P3: `fs` + the four metadata builtins.
- P5a: `env` + `isatty`/`getenv`.
- P6a: `terminalWidth` wrapper in `fs`/`io` if grid needs it.
- `fs` remains a leaf: `eza` computes entry paths with `paths::join`; `fs::Dir`
  stores its operand path but does not import `paths`.
- No mutable module globals. Eza keeps state in `main`/helper locals.
- The review's non-blocking 16 assigned env behavior to P3. This design's
  staging defers `getenv` to P5a, so **P5a owns env behavior and tests**; P1
  only leaves the module out. If the user chooses one larger metadata feature
  in P3, P3 owns it.

---

## 5. Stdlib module contracts

### 5.0 Ownership, borrowing and errors (every module)

| Position | Rule |
|---|---|
| `s: string` | borrowed bytes; callee does not free or retain except by storing (a reference is taken as usual) |
| `x: &T`, `xs: &[]T` | borrowed; no free/move of the object or elements |
| `mut x: &T`, `mut xs: &[]T` | borrowed mutable; assignment/append may free old values; effect summaries propagate |
| return `string` | reference-counted value; caller's owner releases one reference at scope exit |
| return record/array/Result | owned by caller; assignment moves |
| return `?[]byte` | nil absent; non-nil empty present-empty |
| fd `int` | copied scalar; opener closes; Reader/Writer/Dir borrow |
| no `&T` return | accessors return scalars/copies or fill a caller-owned record |
| failure | `Result[T]`, err code positive errno or `EINVAL`/`EIO`/`ENOTDIR` |
| success without value | `Result[bool]` `ok(true)` |
| `try` | in stdlib functions returning `Result`, not inside a larger expression (a `let`/`var` initializer, an assignment source, a statement or a tail expression is allowed); not in `main` |
| globals | constants and pure functions only |

Messages are English. Low-level functions may return runtime text; eza formats
its own diagnostics from `code` (section 7.10).

### 5.1 `strings`

```obo
module strings

pub fn split(s: string, sep: string): Result[[]string]
pub fn splitByte(s: string, sep: byte): Result[[]string]
pub fn join(parts: &[]string, sep: string): string
pub fn trim(s: string): string
pub fn trimLeft(s: string): string
pub fn trimRight(s: string): string
pub fn cmp(a: string, b: string): int                 // unsigned byte order, -1/0/1
pub fn eq(a: string, b: string): bool
pub fn startsWith(s: string, prefix: string): bool
pub fn endsWith(s: string, suffix: string): bool
pub fn contains(s: string, sub: string): bool
pub fn indexOf(s: string, sub: string): int           // byte index, -1 absent
pub fn byteAt(s: string, i: int): byte
pub fn fromBytes(data: []byte): string                // moves the array; copies bytes
pub fn lowerAscii(s: string): string
pub fn natcmp(a: string, b: string): int              // natord compare, case-sensitive
pub fn natcmpFold(a: string, b: string): int          // natord compare_ignore_case, ASCII fold
pub fn quoteName(name: string, quoteSpaces: bool): string
pub fn parentPrefix(parent: string, quoteSpaces: bool): string  // whole parent + '/'; root is '/'
pub fn isSpace(b: byte): bool                         // ' ', 0x09..0x0D
pub fn isBlank(b: byte): bool                         // ' ' and '\t'
pub fn isPrint(b: byte): bool                         // 0x20..0x7E
pub fn isWordByte(b: byte): bool                      // 0x21..0x7E
pub fn isDigit(b: byte): bool                         // '0'..'9'
pub fn displayWidth(s: string): int                   // E0: len(s); E2: UTF-8
```

**P1 compatibility (rebased).** The merged module already has `isSpace`,
`isBlank`, `isPrint`, `isWordByte`, `cmpBytes`, `errText`, `digits` and
`appendPadded`. P1 adds the declarations above and keeps `cmpBytes`, `errText`,
`digits`, `appendPadded` with unchanged semantics until P2, so the merged
`programs/{cat,wc,sort}.obx` compile and behave identically after P1. `cmp` is
new and is the only byte-order name that survives P2.

Semantics:

- `split("", ",")` → `[""]`; `split("a,", ",")` → `["a", ""]`;
  `split(",", ",")` → `["", ""]`; `split("", "")` → `err(EINVAL)`. Byte-wise;
  no UTF-8 decoding. `splitByte` is the single-byte fast path.
- `join([], ",")` → `""`; `join(["a"], ",")` → `"a"`.
- `trim` uses `isSpace` (C locale); one-sided forms.
- `cmp` is the old `cmpBytes`: unsigned byte order.
- `fromBytes(data: []byte)` takes the array **by value**: the call moves the
  array into the callee, `string(data)` copies the bytes, and the callee's
  array is freed at callee scope exit. The caller must not use the array after
  the call. There is no zero-copy borrowed `&[]byte` view; a caller that still
  needs the bytes must copy them first or pass a different owned array.
  Empty array → `""`.
- `displayWidth` is `len(s)` in E0; its signature does not change when UTF-8
  width lands. Callers that lay out columns must not assume bytes==width in E2.

#### 5.1.1 `natcmp` / `natcmpFold` — the eza name order (B5)

The reference algorithm is **natord-plus-plus 2.0.0**, the crate eza 0.23.3
depends on (`Cargo.toml`; source read during this design). `strings::natcmp`
implements `natord::compare`; `natcmpFold` implements
`natord::compare_ignore_case` for the ASCII subset. Both return `-1`, `0`, `1`.

Exact algorithm (E0 ASCII form; Appendix A has the full pseudocode):

1. Walk both strings as sequences of bytes (E0: bytes; E2: UTF-8 code points).
2. At the top of the non-digit loop, skip **whitespace** (`isSpace`: space and
   `0x09..0x0D`) on each side.
3. If both ended, equal; if one ended, it is less.
4. If both current bytes are digits:
   - If either run starts with `0`, compare the first digits; if unequal, that
     decides. Then consume both runs digit by digit; if one run ends first it is
     less; if both end equal, continue the outer loop.
   - Otherwise compare the first digits as `lastcmp`; consume both runs; if one
     run ends first it is less; when both end, `lastcmp` decides (the first
     differing digit, or equal).
5. Otherwise compare the current (folded) bytes and advance one byte each.
6. Case folding is ASCII only (`A..Z` → `a..z`). Non-ASCII bytes compare by
   byte value in E0; eza lowercases Unicode and compares decoded chars. Exact
   ordering fixtures are ASCII; non-ASCII divergence is E2 (section 8.4).

Test table (from the natord crate's own tests, which E0 must reproduce):
`["a","a0","a1","a1a","a1b","a2","a10","a20"]`,
`["x2-g8","x2-y7","x2-y8","x8-y8"]`,
`["1.001","1.002","1.010","1.02","1.1","1.3"]`, the long `1-02 … x8-y8`
list, plus `A a B b`, `2 10`, `rfc1 rfc2 rfc10`, `01 1`, `0 00`, and
whitespace variants. `--sort=Name` uses `natcmp`; the default uses
`natcmpFold`. Equality-class boundary tests (not strict orders):
`natcmpFold("A","a") = 0`, `natcmpFold("README","readme") = 0`,
`natcmp("pic4","pic 4") = 0`, `natcmp("a"," a") = 0`; a stable sort of
`[A,a,B,b]` under fold keeps `A,a,B,b`.

#### 5.1.2 `quoteName` — eza's `QuoteSpaces` rule (B6)

`quoteName(name, quoteSpaces)` returns the bytes eza would display for a name.
The rule is verified from `src/output/escape.rs`:

1. `needs = contains(name, ' ') || contains(name, '\'')`.
2. If `quoteSpaces && needs`: opening/closing quote is `"` if the name contains
   `'`, otherwise `'`.
3. Each byte:
   - `0x09` → `\t`; `0x0A` → `\n`; `0x0D` → `\r`;
   - any other byte `<0x20` or `==0x7f` → `\u{h}` with lowercase hex and no
     leading zeros (`0x00` → `\u{0}`, `0x1b` → `\u{1b}`, `0x7f` → `\u{7f}`);
   - every other byte is emitted unchanged, including `\`, `"`, `'`, and bytes
     `>=0x80`.
4. Quotes are added after escaping; embedded quotes are **not** escaped. Thus
   `a b` → `'a b'`, `it's` → `"it's"`, `a b'c` → `"a b'c"`,
   `a"b c` → `'a"b c'`, `a"b'c d` → `"a"b'c d"` (reference quirk),
   `tab\there` → `tab\there` (escaped, no quotes), `x\x1by` → `x\u{1by}`.
5. `quoteSpaces=false` (`--no-quotes`, E1) suppresses quote characters but
   still escapes controls.
6. E0 names are valid ASCII/UTF-8; eza lossy-decodes invalid UTF-8 before this
   step (section 7.12). `quoteName` operates on bytes and does not validate.
7. `quoteName` is a whole-string primitive. An operand or target **parent
   prefix** calls it **once on the whole parent string** with the outer style;
   it is never applied per path component. A parent `x y/sub` is therefore
   `'x y/sub'`, and the `/` between parent and name is added afterwards
   (§7.6).
8. `parentPrefix(parent, quoteSpaces)` is the shared prefix primitive
   (`add_parent_bits`' root branch): `quoteName(parent, quoteSpaces)` once;
   `""` for an empty parent; `"/"` for parent `"/"` (the escaped root already
   ends in `/`, so **no** second separator); otherwise the escaped parent
   followed by `/`. It does not append the name. Test `"/"`, `""`, `"/tmp"`,
   `"/tmp/x y"`, `"x y"`, `"x y/sub"`, and the quoted `'x y/sub'/` form.
   `parentPrefix("/tmp/x y")` is `'/tmp/x y'/` (the whole parent is quoted
   once); this is a primitive table test, not a target-display golden. The
   design does not create host paths outside its fixture directory: no
   `/tmp/x y/target` and no `tar get` sibling in the case directory's parent,
   so `../tar get` is a broken target (§7.6) and never uses this valid-target
   prefix path.

### 5.2 `paths`

```obo
module paths

pub fn isAbsolute(p: string): bool
pub fn join(a: string, b: string): string
pub fn ext(p: string): string
pub fn hasTrailingSlash(p: string): bool
pub fn isDotName(p: string): bool              // exactly "." or ".."
pub fn lastComponent(p: string): string        // Rust Path::components last; "/" for "/"
pub fn parentOf(p: string): string             // Rust Path::parent shape; "" when none
```

Lexical only: no filesystem, no `~`, no glob, no shell, no symlink resolution.
`/` is the only separator. **`base`, `dir`, `stem` and `clean` are removed**
from the v2 API: they had no consumer in eza or the tools, and their shell/
GNU-shaped semantics (`base("")="."`, `dir("a")="."`) would be a trap beside
`lastComponent`/`parentOf`, whose contract is Rust `Path::components`. If a
future tool needs them, add them with a separate documented contract.

`join` must match the Rust `Path::join` shape used by eza for entry paths:

| call | result |
|---|---|
| `join("", "a")` | `"a"` |
| `join("a", "")` | `"a/"` (Rust `Path::join` appends the separator for an empty second path; verified with rustc 1.87) |
| `join("a", "b")` | `"a/b"` |
| `join("a/", "b")` | `"a/b"` |
| `join("/", "b")` | `"/b"` |
| `join("a", "/b")` | `"/b"` (absolute second replaces) |
| `join("a", "../b")` | `"a/../b"` (no cleaning) |
| `join(".", "..")` | `"./.."` |
| `join("/", "..")` | `"/.."` |
| `join("dir/", "..")` | `"dir/.."` |

No E0 caller joins an empty entry name (a directory entry always has one); the
`join("a","")` row exists so the stdlib contract is Rust-shaped and tested.

`ext` is the stdlib extension notion, **not** eza's extension sort. It follows
the Rust `Path::extension` shape and is pinned by its own tests:

| call | result |
|---|---|
| `ext(".bashrc")` | `""` |
| `ext("a.tar.gz")` | `"gz"` |
| `ext("a.")` | `""` |
| `ext("a..b")` | `"b"` |
| `ext("..")` | `""` |
| `ext(".")` | `""` |
| `ext("")` | `""` |

Eza's `File.ext` is different: substring after the **last** dot (`.git` → `git`)
and ASCII-lowercased; `eza` implements it locally as `extForSort(name)`
(section 7.5). Do not use `paths::ext` for `--sort=extension`.

`lastComponent`/`parentOf` are the only path-display primitives. They implement
the lexical `Path::components` rules, not shell globbing:

| call | lastComponent | parentOf |
|---|---|---|
| `"/"` | `"/"` | `""` |
| `"."` | `"."` | `""` |
| `".."` | `".."` | `""` |
| `""` | `"."` | `""` |
| `"a"` | `"a"` | `""` |
| `"a/"` | `"a"` | `""` |
| `"a/b"` | `"b"` | `"a"` |
| `"a//b"` | `"b"` | `"a"` |
| `"a/./b"` | `"b"` | `"a"` |
| `"./a"` | `"a"` | `"."` |
| `"/a"` | `"a"` | `"/"` |
| `"/a/b"` | `"b"` | `"/a"` |
| `"//a"` | `"a"` | `"/"` (probe the double-slash root spelling) |
| `"/tmp/dir/"` | `"dir"` | `"/tmp"` |

P0 probes eza's actual operand display for `./f`, `a//b`, `a/./b`, `dir/`, `/`,
`.`, `..`; the table is the design contract and the probe resolves any
double-slash/root spelling difference. `eza`'s `operandDisplay` (section 7.2)
uses these two functions.

`strings::parentPrefix(parent, outerQuoteSpaces)` (§5.1) is shared by operands
and symlink target parents (the eza layer's `add_parent_bits` root branch):

- escape the **whole** `parent` once with the outer style
  (`quoteName(parent, outerQuoteSpaces)`);
- an empty parent returns `""`;
- if `parent == "/"`, return `"/"` and do **not** append a second separator
  (the escaped root already ends in `/`);
- otherwise return the escaped parent followed by `/`.

The caller appends the name after this prefix: parent `x y` + name `f` →
`'x y'/f`; parent `/` + name `f` → `/f` (not `//f`); parent `/tmp` + name
`f` → `/tmp/f`. A target `/` has an empty parent and name `/`. Quoting is
never per component.

### 5.3 `sort` (ascending, stable; reverse is separate)

No callbacks. All functions return a new owned `[]int` permutation of entry
indices. **No function takes a `reverse` flag.** eza's reverse is a post-sort
vector reversal (ties reversed); a comparator negation would preserve ties and
diverge (B5/new finding 22).

```obo
module sort
import "strings.obx" as strings

pub fn identity(n: int): []int
pub fn orderStrings(keys: &[]string): []int            // unsigned byte order
pub fn orderNatural(keys: &[]string, fold: bool): []int
pub fn orderInts(keys: &[]int): []int                  // ascending
pub fn sortOrderStrings(order: &[]int, keys: &[]string): []int
pub fn sortOrderNatural(order: &[]int, keys: &[]string, fold: bool): []int
pub fn sortOrderInts(order: &[]int, keys: &[]int): []int
pub fn reverse(mut order: &[]int)                      // in-place whole-array reverse
pub fn sortInts(mut values: &[]int)
```

- All `order*`/`sortOrder*` are **stable**; equal keys keep the input relative
  order. `sortOrder*` takes an existing order (entry ids) and returns a new
  order stable with respect to that input order, using `keys` indexed by id.
- `identity(n)` returns `[0,1,...,n-1]`; `n=0` → empty.
- Implementation: iterative bottom-up merge sort with an owned scratch `[]int`;
  O(n log n), no recursion, no per-comparison allocation. Sorting permutes
  indices; it never moves owned strings/records.
- `reverse` swaps the whole order array end-to-end. `[a,b,c]` → `[c,b,a]`.
- `sortInts` is a scalar convenience; eza uses orders only.
- Duplicate keys, empty and one-element inputs are tested. Out-of-range ids in
  a caller-supplied order are caller bugs (trap); stdlib does not validate.
- The GNU `sort -n` numeric parser stays private in `cmd/sort.obx`; it is not a
  stdlib numeric comparison.

Eza pipeline (section 7.5 has the full rules):

1. Start from directory order after filtering (`.`/`..` synthesized first).
2. Name: `orderNatural(names, true)` for default/`name`; `false` for `Name`.
3. Size: `orderInts(sizes)` (stable ascending from directory order).
4. Modified: `orderInts(nsecs)` then `sortOrderInts(order, secs)` (secs primary).
5. Extension: `orderNatural(names, foldForField)` then
   `sortOrderStrings(order, extLower)` then `sortOrderInts(order, hasExt)`.
6. Type: `orderNatural(names, false)` then `sortOrderInts(order, typeRank)`.
7. If `-r`: `reverse(order)`.
8. If `--group-directories-first`: `sortOrderInts(order, dirRank)` (0 dir,
   1 other), applied **after** reverse.

### 5.4 `fmt`

```obo
module fmt
import "io.obx" as io

pub fn intWidth(n: int): int                    // digits, including '-'
pub fn digits(n: int): int                      // non-negative; digits(0)=1
pub fn appendPadded(mut buf: &[]byte, n: int, width: int)
pub fn writeInt(mut w: &io::Writer, n: int, width: int, pad: byte): Result[bool]
pub fn writeIntRight(mut w: &io::Writer, n: int, width: int): Result[bool]
pub fn writeIntLeft(mut w: &io::Writer, n: int, width: int): Result[bool]
pub fn writePadded(mut w: &io::Writer, n: int, width: int): Result[bool]
pub fn writeStringPad(mut w: &io::Writer, s: string, width: int): Result[bool]
pub fn modeString(mode: int): string            // eza's 10-char form
pub fn octal(n: int, width: int): string
```

- `digits`/`appendPadded` are the merged `lib/strings.obx` functions with
  unchanged behavior; `writePadded` is the merged `lib/io.obx` function with
  unchanged output. P2 moves them here. `wc`'s padding must stay
  byte-identical.
- `writeInt` handles negatives (`-` + magnitude); `intWidth(-5)=2`. The
  64-bit minimum is handled explicitly or documented as excluded; test either.
- **`modeString` implements eza's permission type characters, not ls's:**
  regular `.`, directory `d`, symlink `l`, FIFO `|`, socket `s`, block `b`,
  char `c`, unknown `?`; then `rwx` triples with setuid/setgid/sticky as
  `s`/`S` and `t`/`T`. Exactly 10 characters unless the eza layer appends `@`
  for xattrs (E0 fixtures have none, and our tool does not query xattrs).
  Examples: `.rw-r--r--`, `drwxr-xr-x`, `lrwxrwxrwx`, `|rw-r--r--`,
  `srwxr-xr-x`, `.rwsr-xr-x`, `.rwxr-sr-x`, `drwxrwxrwt`, `.rw-r--r-T`.
- No floats, no locale grouping. `--bytes` sizes under `LC_ALL=C` are plain
  digits (section 7.8); if P0 shows grouping, add `fmt::groupDecimal` and use
  it, but do not add it speculatively.
- `octal` is unused by E0; keep only if the P1 API budget allows. It is tiny.

### 5.5 `io`

The merged `lib/io.obx` already exposes the Reader/Writer API as a `pub` module.
P2 **removes `writePadded`** (moved to `fmt`) and **adds `writeByte`**; it does
not recreate the module. `clampChunk` is part of the merged public surface and
stays. `isTty`/`termWidth` are **not** in P2; they arrive with the metadata
features (P5a/P6a).

```obo
module io

pub type Reader = ref record {
    fd: int
    buf: []byte
    pos: int
    size: int
    hitEof: bool
}
pub type Writer = ref record {
    fd: int
    buf: []byte
    size: int
}

pub fn clampChunk(size: int): int              // merged API; clamps to 1..1 MiB
pub fn newReader(fd: int, size: int): Reader
pub fn newWriter(fd: int, size: int): Writer
pub fn fill(mut r: &Reader): Result[bool]
pub fn takeChunk(mut r: &Reader): []byte
pub fn readLine(mut r: &Reader): Result[?[]byte]
pub fn readByte(mut r: &Reader): Result[int]
pub fn readN(mut r: &Reader, n: int): Result[?[]byte]
pub fn eof(r: &Reader): bool
pub fn flush(mut w: &Writer): Result[bool]
pub fn write(mut w: &Writer, s: string): Result[bool]
pub fn writeBytes(mut w: &Writer, data: &[]byte): Result[bool]
pub fn writeByte(mut w: &Writer, b: byte): Result[bool]
pub fn pump(mut r: &Reader, mut w: &Writer): Result[bool]
pub fn count(mut r: &Reader): Result[int]
```

- fd is borrowed; Reader/Writer own only buffers and never close. `fill` treats
  only `ok(nil)` as EOF; a short chunk is data. `readLine` strips `\n`, keeps
  `\r`; nil is EOF at line start; non-nil empty is an empty line. `takeChunk`
  moves unconsumed bytes out.
- `write`/`writeBytes`/`writeByte` follow the existing threshold/flush rules;
  `flush` loops short writes and keeps the unwritten tail on error.
- Fields are public because the language has no field privacy; treat them as
  API. Do not add a second Writer representation in eza.
- P2's `io.obx` has no `isTty`/`termWidth`; P5a adds `pub fn isTty(fd:int):bool`
  after the builtin exists, P6a adds `pub fn termWidth(fd:int):int` if needed.

### 5.6 `cli` (the requested "args" module)

`args` is a universe builtin; an import alias equal to it is rejected by the
alias check, and `import "args.obx" as cli` is a permanent wart. The module is
**`cli`**.

```obo
module cli

pub const VALUE_NONE = 0
pub const VALUE_REQUIRED = 1
pub const VALUE_OPTIONAL = 2

pub type FlagSpec = ref record {
    short: byte          // 0 = no short form
    long: string         // canonical long name, never ""
    mode: int            // VALUE_NONE / VALUE_REQUIRED / VALUE_OPTIONAL
    consume: []string    // optional: values consumed as a separate next token
    accept: []string     // values accepted after '=' or attached; empty = any
    choices: []string    // text for error/help choices; empty = no choices text
    default: string      // optional: value when no value is given
}
pub type Option = ref record {
    short: byte          // the occurrence's short form, or 0
    long: string         // the matched long name
    value: string        // "" when none; use hasValue
    hasValue: bool
}
pub type Parsed = ref record {
    options: []Option
    operands: []string
}

pub fn argv(): []string
pub fn parse(args: &[]string, from: int, specs: &[]FlagSpec): Result[Parsed]
pub fn shortName(a: string, i: int): byte
pub fn isLong(a: string): bool
pub fn longName(a: string): string
pub fn longValue(a: string): string
pub fn hasLongValue(a: string): bool
```

**Ownership.** `FlagSpec` arrays are caller-owned and must outlive `parse`.
`Parsed` and its `Option` records are owned by the caller. `parse` **copies**
each option long name, value and operand into independent strings (via
`substr`) so `Parsed` does not retain references into `argv` or the specs; the
cost is tiny and the lifetime rule is simple. `Option.value` is a copied
string; `Parsed.operands` are copied strings. `argv()` is a thin wrapper over
`args()` and **includes argv[0]**; eza calls `parse(argv, 1, specs)`. `from` is
the index of the first token to parse; `0 <= from <= len(args)` is required,
`from=len` yields no tokens, and negative/out-of-range is `err(EINVAL)`.

Parsing rules (pinned from eza 0.23.3's `src/options/parser.rs` plus the
`FlagSpec` contract):

- `from` bounds: `0 <= from <= len(args)`; otherwise
  `err("invalid argument start", EINVAL)`. Negative or > len is tested.
- Stop option parsing at `--`; following tokens are operands. Options are
  **interspersed** with free operands until `--`: a token beginning `-`/`--`
  is parsed as an option wherever it appears, not only before the first
  operand. `eza d -a` lists the hidden entries of `d`; `eza d --sort=name`
  sorts that listing. A parser that stops at the first operand is wrong; test
  both orders.
- A lone `-` is an operand. `--` with nothing after yields no operands.
- **Equals splitting (`split_on_equals`) applies to short and long tokens
  alike.** Work on the option-name part: for a long token the bytes after the
  leading `--`, for a short token the bytes after the leading `-`. Split at
  the **first** `=` only when the name before it is **non-empty** and at
  least one byte follows it; otherwise do not split and let the long lookup
  or the short-cluster walk run on the whole token (a trailing `=` is an
  ordinary byte). The split never looks past the first `=`, so
  `--classify==` splits into name `--classify` and value `=`.
  - Long: `--sort=name` splits (name `sort`, value `name`);
    `--classify==` splits with value `=`; `--classify=`, `--sort=` and
    `--all=` do not split and are unknown whole tokens
    (`Unknown argument --classify=`/`--sort=`/`--all=`); `--=x` has an empty
    name part before the `=` and is `Unknown argument --=x`.
  - Unknown-name reporting follows the split: a token that is **not split** is
    reported whole (`--foo=`, `--=x`, `--all=`); a **split long** token
    reports only the name before the `=` (`--foo=bar` and `--foo==` →
    `Unknown argument --foo`); a **short cluster** reports its first unknown
    short byte (`-Qfoo` and `-Q=foo` → `Unknown argument -Q`; the rest of the
    cluster or split value is not part of the reported attempt).
  - Short: `-lF=always` splits into cluster prefix `lF` and value `always`;
    `-s=` does not split, so the required `-s` consumes the rest `=` as its
    value (the sort deduction then rejects `=` as a bad value, not "needs a
    value"); `-I=` likewise takes `=` as its value; `-a=` does not split, so
    the cluster is `a` then the unknown short `=` (`Unknown argument -=`);
    `-F=` does not split, and the optional `-F` with a non-empty rest `=` not
    in `consume` is `Flag -F cannot take a value`.
  These outcomes are design rules tested in `tests/stdlib.py`; P0 records the
  reference's bytes for the same spellings.
- Long `--name`, `--name=value` after the split rule above. A long option
  with `VALUE_NONE` and a value (from a split) is
  `err("Flag --name cannot take a value")`. With `VALUE_REQUIRED`, a
  missing value (no `=`, no next token) is
  `err("Flag --name needs a value")`, plus ` (choices: ...)` when
  `FlagSpec.choices` is non-empty. With `VALUE_OPTIONAL`: `--name=value` takes
  `value`; `--name` followed by a token in `consume` takes that token;
  otherwise it uses `default` and the next token remains an operand. If
  `accept` is non-empty, the resulting value must be in `accept`; otherwise
  the parser returns `err("Option --name has no \"value\" setting")` — **no
  choices text for optional flags** (eza's `BadArgument` for
  `TakesValue::Optional` has no choices). For `VALUE_REQUIRED` the same error
  includes ` (choices: ...)` from `FlagSpec.choices`. Eza words the option as
  `Option --long (-s)` when a short form exists; the module formats both and
  tests exact strings.
- Short clusters `-abc`: each byte is a short flag. The equals split above
  runs first on the whole token: with a split, the bytes before `=` are
  walked as a cluster and the **last** short flag in that prefix takes the
  value (so `-lF=always` is `-l` then `-F=always`, validated against
  `accept`). If that last flag is `VALUE_NONE`, the split value is a value on
  a flag that takes none and is rejected (`err("Flag -a cannot take a
  value")` for `-la=value`); P0 records the reference's exact wording for
  that spelling. Without a split there is no special `=value` form:
  `VALUE_NONE` continues the cluster and `=` is just another short byte; a
  `VALUE_REQUIRED` flag consumes the rest of the cluster (including a leading
  `=`) as its value, or the next token when the rest is empty; missing is an
  error. `VALUE_OPTIONAL` at the end of a cluster with no rest uses
  `default`; with a non-empty rest **without a split** it is the value only
  when it is in `consume` (else `err("Flag -F cannot take a value")`); if at
  the end, the next token is consumed only when it is in `consume`. The split
  form takes `value` and validates it against `accept`. Therefore `-F=always`,
  `-F=automatic`, `-Falways` and `-F always` work, `-F=` is
  `Flag -F cannot take a value`, `-s=` is the value `"="` (bad value at
  deduction), and `-a=` is `Unknown argument -=`. `-Fautomatic` and `-Fl` are
  forbidden-value errors (the attached rest is checked against the consume
  list; a split value bypasses that check and the later option deduction
  accepts `automatic`).
- Long optional spellings: `--classify=always`, `--classify always` (consumed
  because `always` ∈ consume), `--classify automatic` (not consumed; the
  default `auto` is used and `automatic` becomes an operand).
  `--classify=` is `Unknown argument --classify=` (empty after `=` does not
  split) and `--classify==` is `Option --classify (-F) has no "=" setting`
  (it splits with value `=`).
- Unknown long: `err("Unknown argument --foo")`; unknown short:
  `err("Unknown argument -X")`. The reported name is the looked-up name, not
  necessarily the original token: no-split tokens are reported whole, split
  long names before the `=`, and a short cluster's first unknown byte (see
  the six exact cases below). `parse` does not print and does not exit; the
  caller prefixes `eza: ` and exits 3.
- Exact error-wording tests: `--sort=foo` → `Option --sort (-s) has no "foo"
  setting (choices: name, Name, size, extension, Extension, modified, changed,
  accessed, created, inode, type, none)`; `--classify=foo` and `--color=foo` →
  `Option --classify (-F) has no "foo" setting` (or `--color`), **no choices**;
  `-Ffoo`/`-Fl`/`-F=` → `Flag -F cannot take a value`; missing `--sort` value →
  `Flag --sort needs a value (choices: ...)`; `--classify=`/`--sort=`/
  `--all=` → `Unknown argument --classify=`/`Unknown argument --sort=`/
  `Unknown argument --all=`; `--classify==` →
  `Option --classify (-F) has no "=" setting`; `-s=`/`--sort==` →
  `Option --sort (-s) has no "=" setting (choices: ...)`; `-a=` →
  `Unknown argument -=`; `--=x` → `Unknown argument --=x`;
  `--foo=bar`/`--foo==` → `Unknown argument --foo`;
  `--foo=` → `Unknown argument --foo=`; `-Qfoo`/`-Q=foo` →
  `Unknown argument -Q`; `-I=` parses with
  value `"="` (E0 then reports `-I` as unsupported); `--icons=foo` → no
  choices.
- Repeated options append one `Option` per occurrence; callers count (`-a`) or
  take the last (`--sort`). No abbreviation, no wildcard, no `--no-` inference.
- No `--help`/`--version` in the parser; they are ordinary unknown names in
  E0, reported by the unknown-name rule (`--help`/`--version` whole;
  `--help=x`/`--version=x` → `Unknown argument --help`/`--version`), exit 3
  (own usage error).
- Worked spellings to test: `-F`, `-F=always`, `-F=automatic`, `-Falways`,
  `-F always`, `-Fautomatic` (forbidden-value error), `-lF`, `-lFalways`,
  `-lF=always` (mixed cluster with `=`), `-F automatic` (operand `automatic`),
  `--classify=automatic`, `--classify automatic` (operand),
  `--classify=`/`--sort=` (unknown argument), `--classify==` (bad value `=`),
  `-F=` (cannot take a value), `-s=` (bad value `=`), `-a=` (unknown `-=`),
  `--all=`/`--=x` (unknown), `--foo=bar`/`--foo==` (unknown `--foo`),
  `--foo=` (unknown `--foo=`), `-Qfoo`/`-Q=foo` (unknown `-Q`),
  `-I=` (value `=`; E0 rejects `-I`),
  `-sname`, `-s=name`, `-s name`, `--sort=name`, `--sort name`, `--`, `-`,
  `-aa`, `-Aaa`, and `d -a`/`d --sort=name` (options after a free operand).

### 5.7 `errors` (P2)

```obo
module errors

pub const EXIT_OK = 0
pub const EXIT_FAIL = 1          // I/O and runtime error
pub const EXIT_PATH = 2          // eza input-path metadata failure (0.23.3 source)
pub const EXIT_USAGE = 3         // command-line problem (not GNU coreutils' 2)
pub const EXIT_PERMISSION = 13   // documented; probe the actual process status

pub fn text(code: int): string               // C-locale strerror subset
pub fn osError(code: int): string            // "<text> (os error N)"
pub fn debugPath(p: string): string          // Rust OsStr Debug quoting for stderr
pub fn report(prog: string, message: string) // "prog: message\n" to stderr
```

- `text` moves `errText` from merged `lib/strings.obx` and extends it with `34`
  (ERANGE); `40` (ELOOP) is already present. Add any further code the metadata
  tests exercise. Fixed English, not locale-aware; the differential pins
  `LC_ALL=C`. Unknown → `error N`.
- `osError` appends ` (os error N)` to match Rust `io::Error` Display, which
  eza uses for path and write errors. Exact cases are ASCII.
- `debugPath` reproduces Rust's `OsStr`/`Path` Debug quoting for simple ASCII
  names (`"name"`); for control bytes it uses escapes; exact stderr fixtures
  use simple names. P0 records the real forms.
- `EXIT_USAGE` is **3**, not 2. `EXIT_PATH=2` is eza's input-metadata status,
  not a usage status; do not reuse it for usage. If the pinned reference's
  permission branch exits 13, use `EXIT_PERMISSION`; if it continues with 0/2,
  mirror the probe and record the divergence from the man page.
- `report` is a convenience, not an exit. `exit` skips `defer` and leak checks;
  eza flushes stdout and closes the current Dir before an error exit where
  practical, and the harness only leak-checks success paths.

### 5.8 `env` (P5a; B3)

```obo
module env

pub fn get(name: string): ?[]byte      // nil absent; non-nil empty present-empty
pub fn has(name: string): bool
pub fn value(name: string): string     // "" absent or present-empty
pub fn getOr(name: string, fallback: string): string
```

- `get` calls the `getenv` builtin, which copies the value into an owned byte
  array. `has` is:
  ```obo
  pub fn has(name: string): bool {
      let v = get(name)
      return v != nil
  }
  ```
  The local is required by the owned-temporary rule; test it.
- `value` copies the bytes to a string; absent and present-empty both give
  `""`; `has` is the way to distinguish. `getOr` returns `fallback` when
  absent.
- **Do not name any helper `string`.** `string` is a primitive type and a
  conversion; a module-level function of that name shadows it in the module
  and breaks later `string`-typed declarations. A compile test declares
  `getOr(...): string`, uses `string(n)`, and declares `value` before and after
  those declarations; all orders must compile.
- Eza uses `env::has("NO_COLOR")`, `env::value("LS_COLORS")`,
  `env::value("EZA_COLORS")` in E1. No `setenv`.
- P1 has no `env.obx`; behavior and tests belong to P5a.

### 5.9 `fs` (P3)

```obo
module fs

pub const STAT_MODE = 0
pub const STAT_SIZE = 1
pub const STAT_MTIME = 2
pub const STAT_MTIME_NS = 3
pub const STAT_UID = 4
pub const STAT_GID = 5
pub const STAT_NLINK = 6
pub const STAT_INO = 7
pub const STAT_DEV = 8
pub const STAT_RDEV = 9
pub const STAT_BLKSIZE = 10
pub const STAT_BLOCKS = 11
pub const STAT_ATIME = 12
pub const STAT_CTIME = 13
pub const STAT_FIELDS = 14

pub const KIND_UNKNOWN = 0
pub const KIND_FIFO = 1
pub const KIND_CHR = 2
pub const KIND_DIR = 4
pub const KIND_BLK = 6
pub const KIND_REG = 8
pub const KIND_LNK = 10
pub const KIND_SOCK = 12

pub const DIR_BUF = 32768

pub type Stat = ref record {
    mode: int
    size: int
    mtime: int
    mtimeNs: int
    uid: int
    gid: int
    nlink: int
    ino: int
    dev: int
    rdev: int
    blksize: int
    blocks: int
    atime: int
    ctime: int
}
pub type DirEntry = ref record {
    name: string
    ino: int
    kind: byte
}
pub type Dir = ref record {
    fd: int
    path: string       // the operand path, owned; used for entry paths
    buf: []byte        // current raw getdents64 buffer, owned
    text: string       // string(buf), owned; used by substr
    pos: int
    used: int
    eof: bool
    size: int          // requested buffer size
}

pub fn open(path: string): Result[Dir]
pub fn next(mut d: &Dir, mut e: &DirEntry): Result[bool]
pub fn close(mut d: &Dir): Result[bool]
pub fn statArray(path: string, follow: bool): Result[[]int]
pub fn stat(path: string): Result[Stat]
pub fn lstat(path: string): Result[Stat]
pub fn readlink(path: string): Result[[]byte]
pub fn typeOf(mode: int): byte
pub fn isDir(mode: int): bool
pub fn isSymlink(mode: int): bool
pub fn isRegular(mode: int): bool
pub fn isFifo(mode: int): bool
pub fn isSocket(mode: int): bool
pub fn isExecutableFile(mode: int): bool     // regular && (mode & 0o100)
pub fn sizeForLong(mode: int, size: int): int // size for regular, -1 otherwise
```

Directory iterator:

- `open` calls `openFile(path,"r")` (O_RDONLY; directories open). A non-directory
  fd gives `ENOTDIR` on the first `readDirFd`. The fd is tracked; `closeFile`
  closes it; OBO_CHECK catches leaks.
- `Dir` owns `buf`, `text`, `path`; borrows `fd`. `close` is idempotent: if
  `fd < 0`, return `ok(false)` without a syscall; otherwise `closeFile`, set
  `fd = -1` regardless, return the Result.
- `next` refills when `pos >= used` and not `eof`: `readDirFd(d.fd, d.size)`.
  `ok(nil)` sets `eof` and returns `ok(false)`. A non-nil chunk replaces
  `d.buf` (freeing the old), sets `d.text = string(d.buf)` (one chunk copy),
  resets `pos=0`, `used=len(d.buf)`.
- Parse `linux_dirent64`: `d_ino` u64 at 0, `d_off` i64 at 8 (unused),
  `d_reclen` u16 at 16, `d_type` byte at 18, `d_name` NUL-terminated at 19.
  Validate `reclen >= 20`, `pos + reclen <= used`, NUL before the end, and
  `reclen != 0`; otherwise `err("bad directory entry", EIO)` without
  advancing (no infinite loop).
- **`next` skips records named exactly `.` or `..`.** eza's Rust `read_dir`
  never yields them; eza synthesizes them (section 7.3). Keeping the skip in
  `fs` makes the iterator's input order match eza's and avoids double dots.
  Document this in `fs` and `LANGUAGE.md`.
- `e.name = substr(d.text, start, end)` — a copy, never an alias of `d.buf`,
  `d.text`, the kernel buffer or the next entry. It survives refills and
  `close`. `e.ino = d_ino`; `e.kind = d_type`; `DT_UNKNOWN` is
  `KIND_UNKNOWN`. **Type resolution:** eza caches
  `DirEntry::file_type().ok()` and, when it is `None`, `File::filetype()`
  lazily calls `symlink_metadata` (lstat). Match that: use `d_type` when known;
  if unknown, lstat the entry path and derive the type from `mode` for
  filtering, type sort, classify and group-directories-first. If lstat fails,
  treat the type as unknown/special (exclude from `-D`/`-f`, rank special,
  no classify marker, not a group directory). P0 probes a DT_UNKNOWN
  filesystem; normal fixtures have known `d_type`, so this path is
  defence-in-depth rather than an exact-golden dependency.
- Buffer 32 KiB. `readDirFd` caps max at 1 MiB (section 6.3). A smaller
  caller-chosen size can make `getdents64` return `EINVAL`; that is an error.
- Memory: one 32 KiB buffer, one 32 KiB chunk string, one name per selected
  entry. Tree opens one `Dir` per depth and closes before descending.

Stat/readlink:

- `statArray(path, follow)` calls `statPath` (follow) or `lstatPath` (no
  follow) and returns the 14-int array unchanged. `stat`/`lstat` build a record
  and let the array free. Eza hot loops use `statArray` and field constants.
- Field order is the design's, not the kernel's; section 6.6 has offsets.
- `sizeForLong`: `isRegular(mode)` → `size`, else `-1`. Eza renders `-` for
  `-1`. This encodes eza's `File::size()` for directories, symlinks, FIFOs,
  sockets and other non-device files. Char/block devices show `major,minor` in
  eza; E0 fixtures avoid device nodes and our tool renders `-` for them
  (documented divergence, section 7.12).
- `readlink` calls `readLinkPath`: raw target bytes, no NUL, 4095-byte limit
  (4096 → `ENAMETOOLONG`); dangling target still succeeds; non-symlink
  `EINVAL`.
- `stat` follows symlinks; `lstat` does not. Both are path-based and resolve a
  relative path against the process cwd. Target path construction (a relative
  target resolves against the entry's directory, or the operand's parent for
  `-d`) and the use of **stat** for target classification/broken detection are
  eza's rules (§7.9); `fs` does not resolve link targets itself.
- No `getcwd` in P3. `Dir.path` is the operand as given; eza computes entry
  paths itself.

### 5.10 `time`

```obo
module time

pub fn formatLongIso(secs: int): string   // "YYYY-MM-DD HH:MM", UTC
pub fn formatIso(secs: int): string       // "MM-DD HH:MM", UTC
pub fn civil(secs: int): []int            // [year, month, day, hour, min, sec, weekday]
pub fn floorDiv(a: int, b: int): int
pub fn floorMod(a: int, b: int): int
```

- Negative values use **floor division**, not truncation toward zero. obo's
  `/` truncates; `floorDiv(a,b)` subtracts 1 when `a % b != 0` and the signs
  differ; `floorMod` returns a non-negative remainder for positive `b`.
  Tests: -1 → 1969-12-31 23:59:59 UTC; -86400; -2208988800 (1900-01-01).
- Civil-from-days is integer arithmetic (standard days-to-civil algorithm); no
  floats, no locale, no leap seconds. Weekday is documented (0 = Sunday).
- UTC only; `TZ` is not read. The differential sets `TZ=UTC`; other user
  timezones are a documented divergence until a timezone phase.
- `long-iso` is `YYYY-MM-DD HH:MM`, minute precision. **Eza's long layer does
  not call this for negative mtimes:** 0.23.3's `SystemTime::duration_since`
  fails before 1970, so the date cell is `-`. Eza checks `mtime < 0` and
  writes `-`; `time` still handles negatives correctly for tests and future
  use.
- Tests: 1969-12-31 23:59:59, 1970-01-01 00:00:00, 2000-02-29 (leap),
  2100-03-01 (not leap), month/year boundaries, pre-1970 floor cases.
- `formatIso`/`civil` are optional; keep if cheap. `full-iso` is out.

---

## 6. Runtime metadata: E0 slots 33–36 (P3)

### 6.1 Slots and staging

Append-only slots, no renumbering of 0–32:

```
P3  (E0)  33 readDirFd(fd,max): Result[?[]byte]
          34 statPath(path): Result[[]int]
          35 lstatPath(path): Result[[]int]
          36 readLinkPath(path): Result[[]byte]

P5a (E1)  37 isatty(fd): bool
          38 getenv(name): ?[]byte

P6a (E2)  39 terminalWidth(fd): int        only if grid needs it
```

- No `currentDir` slot. No `resultCall2`. No `env_base` in P3.
- P5a/P6a are separate small compiler features with their own full gate and
  promote. This keeps each compiler diff auditable and avoids a large P3.
- If the user prefers one metadata feature, keep the same order and cut
  `terminalWidth` first, then `getenv`; never raise `PEAK_BUDGET`.

### 6.2 Compiler/runtime constraints

- `rtCall` refuses >4 arguments (find the `rtCall` declaration by name; line
  numbers shift with every compiler edit). A Result-returning call's last two
  arguments are the shared err descriptor and the cached ok variant.
  Therefore `statPath`/`lstatPath`/`readLinkPath` use `resultCall3` (one user
  arg + 2 descriptors) and `readDirFd` uses `resultCall4` (two user args + 2).
- Descriptor identity: the ok variant must be
  `derived("result", payload).variants[0].desc`, exactly as `resultCall3/4`
  do; the err descriptor is `errVariantType().desc`. A fresh descriptor makes
  `match` miss both arms. No new descriptor kind.
- Payloads: `[]int` is an owned int array (`rt_alloc_array(14,1)`);
  `[]byte` is an owned byte array (`rt_alloc_array(n,4)`); `?[]byte` is 0 or an
  owned array. Result cells are runtime-built with the two-allocation rule
  (`tmp_root[0]` while allocating the cell).
- No runtime-retained program pointers. Every byte/string is copied into an
  obo object.
- `cpath` converts a string to a NUL-terminated path in a 4096-byte stack
  buffer; it rejects empty paths, embedded NULs and paths of 4096+ bytes.
  Stat/readlink use it. The 4095-byte limit is part of the API.
- `pinStr` pins a string argument before the runtime call, as
  `openFile`/`readFile` do. One user argument means no later-argument hazard,
  but pinning is uniform and cheap.
- `readDirFd` materialises fd before max (as `readFd` does). `statPath` etc.
  materialise/pin their single argument.
- fd table: `readDirFd` uses the existing table. A tracked closed fd under
  `OBO_CHECK=1` is exit 9; otherwise `err(EBADF)` with no syscall. The path
  builtins do not use fds.
- Slot table: `entry.S` lists slots twice (the `slot` macro and the `check`
  invocations). Append in both sequences in the same commit; a wrong order
  jumps to a valid-aligned wrong function. Tests call every slot; mutants swap
  adjacent slots.

### 6.3 Builtin behaviour

**`readDirFd(fd, max)` → `Result[?[]byte]`**, runtime
`u64 rt_read_dir_fd(i64 fd, i64 max, u64 descErr, u64 descOk)`.

- `flush()` text output first, as `readFd` does.
- Checks before allocating:
  1. `max <= 0` → `err("readDirFd needs a positive count", EINVAL)`;
  2. `max > 1048576` → `err("readDirFd count is too large", EINVAL)`
     (`MAX_DIR_BUF = 1 MiB`; a huge constant cannot OOM the process);
  3. `fd < 0` → `err("cannot read dir fd N", EBADF)`;
  4. tracked closed fd: OBO_CHECK exit 9; otherwise `err(..., EBADF)`.
- Allocate `rt_alloc_array(max,4)`; call `getdents64(fd,data,max)` with EINTR
  retry.
- `r < 0`: free, `err(message, -r)`. `r == 0`: free, `ok(nil)` (EOF, never an
  empty array). `r > 0`: set length `r`, `ok(array)`; a short buffer is data.
- The kernel returns whole records; the caller validates.

**`statPath(path)` / `lstatPath(path)` → `Result[[]int]`**, runtime
`rt_stat_path(u64 path, u64 descErr, u64 descOk)` and
`rt_lstat_path(...)`, both calling a shared `do_stat(path, flags, ...)`.

- `cpath`; 4096+ bytes → `err("cannot stat <path>", ENAMETOOLONG)`; empty/NUL →
  `EINVAL`. Use "lstat" in the lstat message; eza formats its own text.
- Allocate `rt_alloc_array(14,1)`; call
  `newfstatat(AT_FDCWD, path, &st, flags)` with flags 0 (stat) or
  `AT_SYMLINK_NOFOLLOW 0x100` (lstat), with EINTR retry. This needs `sys4`
  (`r10`).
- On error free and `err(message,-r)`; on success fill the 14 ints in the
  section 6.6 order and `ok(array)`.
- Atime/ctime are seconds only; mtime has seconds + nanoseconds. The raw
  struct is not retained.

**`readLinkPath(path)` → `Result[[]byte]`**, runtime
`rt_read_link(u64 path, u64 descErr, u64 descOk)`.

- `cpath`; message `"cannot readlink <path>"`.
- `readlink(path, buf, 4096)` with EINTR retry into a 4096-byte stack buffer.
- `r < 0` → `err(..., -r)` (EINVAL non-symlink, ENOENT missing, etc.).
- `r == 4096` → `err(..., ENAMETOOLONG)`, never silent truncation; the v1
  limit is 4095 bytes.
- Otherwise allocate `rt_alloc_array(r,4)`, copy, `ok(array)`; no NUL. An empty
  target (not normally creatable) is a non-nil empty array.

### 6.4 `sys4` and EINTR

- Add `static i64 sys4(i64 n, i64 a, i64 b, i64 c, i64 d)` with `r10` for the
  fourth argument, clobbering `rcx`/`r11`/memory. `newfstatat` uses it; every
  other new call uses `sys3`.
- **EINTR retry:** `getdents64`, `newfstatat`, `readlink` (and later `ioctl`)
  retry on `-EINTR`, exactly like `io_read`/`io_write`. Tests use a test-only
  hook: `OBO_META_FAULT=eintr` (gated by `OBO_CHECK=1`, like `OBO_IO_FAULT`)
  injects one synthetic EINTR per call kind; the call must still succeed. A
  mutant that removes a retry loop is killed by this test. Document the hook as
  test-only and inert unless `OBO_CHECK=1`.
- Runtime data growth P3: one int for the fault flag (plus alignment), no
  `env_base`. Measure text.

### 6.5 Compiler changes

In `obo/src/obo.obo`:

1. Add the four names to `universe` (the `words(...)` list in the `universe`
   construction; find it by name): `readDirFd statPath lstatPath
   readLinkPath`.
2. Add them to `callExempt` (the `scanFunction` builtin word list) and to the
   non-int-return negative list in `plausible` (all four return non-scalars).
3. Add branches in `builtin(name)`:
   - `readDirFd`: fd with `nestedExpr`, `isInt`, materialise; comma; max with
     `nestedExpr`, `isInt`, reject a constant `<= 0` and a constant
     `> 1048576`; `expect T_RPAREN`; `resultCall4(RT_READ_DIR_FD, fd, max,
     derived("opt", derived("array", tByte)))`.
   - `statPath`/`lstatPath`: one string path, `pinStr`, `resultCall3` with
     `derived("array", tInt)`.
   - `readLinkPath`: one string path, `pinStr`, `resultCall3` with
     `derived("array", tByte)`.
   - Error messages follow the existing style, e.g.
     `statPath needs a string path, not X`.
4. No `resultCall2`; no new type/descriptor helper.
5. The compiler source must not use the new builtins (two-round rule); tests,
   `fs` and eza do.
6. `doc_check` examples and `LANGUAGE.md` builtin table updated. Adding a
   `// example:` block changes the doc-example count; the gate says "all
   examples pass" and records the new count (45 at the current baseline) rather
   than hardcoding 45.

### 6.6 Stat layout and field order

x86-64 Linux `struct stat` offsets used by the runtime:

```
st_dev 0 (u64), st_ino 8 (u64), st_nlink 16 (u64), st_mode 24 (u32),
st_uid 28 (u32), st_gid 32 (u32), st_rdev 40 (u64), st_size 48 (i64),
st_blksize 56 (i64), st_blocks 64 (i64),
st_atim 72 (sec i64, nsec i64), st_mtim 88, st_ctim 104; sizeof 144.
```

The 14-int array order is `mode, size, mtime, mtime_ns, uid, gid, nlink, ino,
dev, rdev, blksize, blocks, atime, ctime`. `mode` is the raw `st_mode`
including type bits; use `mode & 0xF000` and `mode & 0xFFF`. `size` is signed.
`ino`/`dev`/`rdev` are copied as 64-bit values; inode values above `INT64_MAX`
are unsupported (not reachable on normal Linux filesystems). atime/ctime are
seconds only.

### 6.7 Paths, races and limits (B11)

- `eza` computes `entryPath(operand, name)` with `paths::join` and passes that
  string to `fs::statArray`, `fs::readlink`, `fs::open` (tree child) and the
  error formatter. Never stat a bare entry name against cwd.
- For synthesized dots: `entryPath` for `.` is the operand itself; for `..` it
  is `paths::join(operand, "..")`, matching eza's `dir.path.join("..")`.
- **Limit:** `cpath` rejects a joined path of 4096+ bytes. A short operand plus
  a 255-byte name can exceed it. E0 exact cases use joined paths <4096. A long
  joined path produces an error from the builtin (`ENAMETOOLONG`) where eza's
  `DirEntry::metadata()` (fstatat on the directory fd) would succeed. Document
  this in `cmd/README.md` and `FORK.md`.
- **Race:** the entry can be replaced between `getdents64` and the path-based
  stat/readlink. Eza's std `DirEntry` operations use `fstatat` on the dirfd and
  avoid the path race for entries. Our v1 API does not. Tests do not mutate
  concurrently. The future fix is `statAt(dirfd,name)`/`readlinkAt(dirfd,name)`
  with `fs::Dir.fd`; it also removes the length limit. Do not add a raw
  `syscall` builtin now.
- `Dir.path` is a copy of the operand string; tree child `Dir`s store the
  joined child path. The path is owned by the Dir and freed with it.

### 6.8 Tests, mutants and memory

Runtime C harness (`tests/rt_meta_test.c`, linked like `rt_io_test.c`, own
`_entry`):

- **stat:** create a file with known size/mode/mtime; check all 14 fields and
  offsets; lstat vs stat on symlink-to-file and symlink-to-dir; missing ENOENT;
  empty EINVAL; 4096-byte path ENAMETOOLONG; directory mode type; mtime nsec;
  pre-1970 mtime signed; EINTR injected once per call.
- **readlink:** file target bytes; dangling; non-symlink EINVAL; missing
  ENOENT; 4095-byte target succeeds and 4096 reports ENAMETOOLONG (create a
  long target if the filesystem allows; otherwise document); EINTR.
- **readDirFd:** real directory returns records then EOF nil; max 0/negative
  EINVAL before allocation; max 1 MiB accepted; max 1 MiB+1 EINVAL; negative
  fd EBADF; closed tracked fd exit 9 under OBO_CHECK; regular-file fd ENOTDIR;
  corrupt-record branches via a crafted buffer test if possible; no leak.
- **N1/N2 order:** max cap, fd, table, allocation; a bad max never allocates.
- **Ownership:** Result descriptors/free for `[]int`, `[]byte`, `?[]byte`;
  OBO_CHECK leak/double-free clean on success paths.

Program tests (`tests/meta/*.obx`, run by `tests/meta_test.py` with
`OBO_CHECK=1` and `OBX_GC=poison`):

- list a directory and compare names/kinds/inos with Python `os.listdir` /
  `os.stat`; assert `.`/`..` are skipped by `fs::next`;
- stat/lstat fields vs Python `os.stat(..., follow_symlinks=...)`;
- symlink target bytes; dangling;
- close idempotence; a missing close fails a dedicated bad case (exit 8), not
  normal cases;
- entry paths for an operand outside cwd and a `../` operand;
- open/close a directory under `OBO_CHECK=1` with no leak.

Compiler tests:

- `tests/errors/meta.obx`: wrong types/counts; `readDirFd` constant 0/negative
  and >1 MiB; non-string paths; extra args; calls in unsupported positions.
- `tests/run/meta.obx`: each builtin compiles/runs; consuming/non-consuming
  `match`; `try` propagation; `Result[[]int]`/`Result[?[]byte]` ownership.
- `port_compare.py`/`port_test.py`: same cells/tags; token dump unchanged.
- `doc_check.py` examples.

Mutants, runners and counts (P3):

- Add **`tests/mutate_meta.py`** with **two independent runners**, because a
  mutated runtime cannot kill a compiler-source mutant (the compiler has
  already made the decisions by the time the runtime runs) and a mutated
  compiler cannot test the runtime's syscall behaviour. It is a separate
  runner; `mutate_io.py` is not stretched to cover metadata code.
  1. **Runtime/`entry.S` runner**, modelled on `mutate_io.py`: for each
     runtime mutant, copy `runtime.c` (and `entry.S` for slot mutants) into a
     private directory, write the mutant text (asserting it differs), build it
     with the Makefile flags, and compile/run the runtime corpus against that
     runtime: `tests/rt_meta_test.c` (direct C contract) and the fs-level
     program cases `tests/meta/*.obx`. Classes: `statPath`/`lstatPath` flag
     swap; each field offset shifted by one; mode/size/uid/gid swapped;
     mtime/mtime_ns swapped; `readDirFd` short=EOF; EOF=empty array; no max
     cap; no fd-table check; `readLinkPath` truncates; readlink length 4096
     accepted; EINTR retry removed; `sys4` uses `rcx` instead of `r10`;
     `entry.S` slot order swapped (both sequences). The runner also runs a
     representative I/O case for any `entry.S` slot mutant, so a wrong slot
     order cannot pass.
  2. **Compiler-source runner**, modelled on `mutate_port.py`: for each
     compiler mutant, write `src/obo.obo` with the mutation into a private
     file (asserting exactly one match per edit and that the text differs),
     compile that source with the unmutated stage compiler into a private
     mutated compiler executable (the same build-the-mutant-source step
     `mutate_port.py` uses for its port binary), and use that executable as
     the compiler for the corpus, with a private sym-dir per case:
     `tests/errors/meta.obx` (must still be refused with the expected
     diagnostic), `tests/run/meta.obx` and `tests/meta/*.obx` (must compile,
     run and produce the expected values). Each mutant names its expected
     diagnostic or observable result in the corpus. Classes: argument check
     removed; fd not materialised; path not pinned; wrong payload type
     (`?[]byte` vs `[]byte`); wrong slot constant; `callExempt` omission;
     `plausible` omission. A mutant that compiles a refused program, misses
     the expected diagnostic, or changes a program result is killed. If one is
     genuinely equivalent, record it with the reason as `mutate_fork.py` does
     (527/549 + 22) and exclude it from the killed count; do not hide it.

The **fd-materialisation mutant's killing case is explicit**: `tests/run/meta.obx`
includes a `readDirFd` call whose `max` argument expression changes the fd
through a `mut &int` reference before the call is made, mirroring
`readFd`'s materialise-fd-before-max rule. A compiler that passes the fd
un-materialised (or evaluates `max` before materialising the fd) observes the
changed value, so the program's result differs and the mutant is killed. The
**"path not pinned"** mutant may be genuinely equivalent with one argument (a
single argument has no later-argument hazard, so uniform `pinStr` is not
observable); if so, record it with the reason and exclude it from the killed
count exactly as `mutate_fork.py` does with its 22 equivalents. Do not claim a
kill without a case that distinguishes it.

**Mutant-anchor uniqueness with `mutate_io`.** The new `readDirFd` runtime code
can naturally repeat `mutate_io.py`'s exact anchor lines (the short-read
assignment `a->len = r`, or the fd-table condition
`tracked && fdstate[fd] == 2`). A duplicated line makes the existing mutant
STALE and the gate fail (safe, but the design asserts 27/27). Keep the new
runtime code textually distinct, or factor the shared check into a helper
(e.g. `check_fd(fd)`), or re-anchor `mutate_io.py`'s pattern in the same
commit. Record the measured `mutate_io` count after the cold run; if a mutant
legitimately moves, re-anchor it and record the new count — never skip a
stale mutant.
- Cases are separated by stage. **P3 `tests/meta/*.obx` covers fs-level
  primitives only:** `fs::open`/`next`/`close`, `fs::stat`/`lstat` field
  values, `fs::readlink` bytes, `paths::join` entry paths, `.`/`..` skipped by
  `fs::next`, and close idempotence. The eza CLI behaviours `-d sub/link`,
  target-is-symlink and chain/loop resolution are **P4** cases in
  `tests/eza.py`; they need `cmd/eza.obx`, `fs` explicitly does not resolve
  link targets (§5.9), and eza does not exist in P3.
- P5a extends `tests/mutate_meta.py` with `isatty`/`getenv` mutants (both
  runners) and their cases; P6a extends it with `terminalWidth`. The metadata
  gate is never satisfied by the I/O runner.
- **Counts are measured, not fixed in advance.** `mutate_io.py` stays 27/27
  (20 runtime + 7 library) and covers only the I/O runtime/library; it gains no
  mutant for the new `io::writeByte`, `fmt` or `errors` in P2 (the coverage
  boundary is stated in §10.3). P3's gate is
  `tests/mutate_meta.py: all mutants killed` with the runtime/`entry.S` and
  compiler-runner counts printed separately and recorded after the cold run;
  P5a/P6a report their own totals. The gate text says "all metadata mutants
  killed (N/N, N recorded after the cold run)", never "27/27" for metadata.

Memory: measure the port's live peak compiling itself after P3. Current
baseline 1,636,176 live / 35,076,608 allocated, budget 2,000,000 (spare
363,824). The four branches are small, but do not promise a size; measure. Cut
order if the peak approaches the budget:

1. merge stat/lstat into one slot with a flags argument (still `resultCall4`);
2. stop and report to the user. **Do not cut `readLinkPath` while E0 symlink
   targets remain**: default/`-1`/`-d`/long render `name -> target`, so cutting
   it silently undoes the v2 fix and makes §7.1/§7.6/§7.7 unimplementable. If
   the user directs a cut, E0 drops symlink target display and its cases and
   the design records an explicit roadmap stop-condition violation (not
   parity);
3. never raise `PEAK_BUDGET`.

### 6.9 `OBO_VERSION`, `.sym` and rebuilds

- P3 bumps `OBO_VERSION` (Stage 2 versioning rule). `obosym` format and `abi`
  do not change: no `.sym` grammar/layout, calling convention, descriptor or
  initialization semantics changes. The runtime slot table is part of the
  compiled program, not `.sym`.
- **The bump touches `tests/sym_test.py`:** its module-level
  `OBO_VERSION = '0.2'` constant and its literal `compiler "0.2"` expectations
  in `stale_missing_corrupt_versions()` (the compiler-mismatch case),
  `handwritten_interface()` and `b6_stub_completion()` must move together.
  Each bump (P3 `0.2`→`0.3`, P5a `0.3`→`0.4`, P6a `0.4`→`0.5`) updates that
  file in the same commit; otherwise `--version` and the mismatch case fail.
  Run `python3 tests/sym_test.py` (27/27) and `python3 tests/sym_fuzz.py`
  (200/200) as part of the full compiler gate.
- After the bump, every `.sym` produced by the pre-bump compiler is invalid for
  `--check`. Rebuild tools/tests from source; do not run `--check` against
  Stage 2 sym dirs. Document the rebuild step in `FORK.md`.
- The seed is promoted once after P3's full gate (`make promote`), not before.
- P5a/P6a repeat the bump/rebuild/promote cycle with their own gate, including
  the `tests/sym_test.py` version constants.

---

## 7. `eza` E0

### 7.1 Deliverable and non-TTY statement

E0 is a **non-TTY** tool: stdout is a pipe or file. It implements one operand
(or none), one **entry** per line, the hidden/`-a`/`-A` rules, the E0 sort
fields with eza's natural name order, post-sort reverse, directory grouping,
quoting, `-F`, `-D`/`-f`, `-d`, the restricted long mode, symlink display, and
the error/exit classes in section 7.10.

**Default/`-1` lines include symlink targets.** eza's `lines.rs` calls
`.with_link_paths()` for every file, so the non-TTY default, `-1` and `-d`
render a symlink as `name -> target` (outer name quoting; target parent prefix
with outer style; target name always `QuoteSpaces`; broken target escaped with
the outer style; an unreadable/error target shows no arrow). `-F=always`
appends the classify marker to the **target** in lines/default (`link ->
target/`, `link -> prog*`), and no marker for broken/error targets. Grid (E2)
is the only view that omits target paths and puts the marker on the link
(`link@`); long/tree also show targets and mark the target. Tree, colour and
grid are E1/E2; on a TTY E0 still prints lines (with targets) and has no
colour, a documented staged divergence until E2.

Display is not classification. The target **path used for metadata** is
resolved per §7.9 (a relative target against the entry's directory, or the
operand's parent for `-d`; an absolute target as written), and the
valid/broken decision — and therefore whether a marker is appended — uses
**stat (follow)** on that path. The link's own mode/size/mtime/uid and long
cells still come from **lstat** on the entry path.

`main` returns normally on success so `rt_finish` prints stats and runs leak
checks. Error paths flush stdout, close the current `Dir` where practical, and
`exit(code)`; `exit` skips `defer` and leak checks, so memory/leak gates use
success cases only.

View flags follow eza's last-view-flag rule (verified source comment in
`Mode::deduce`): `-l` and `-1` are both accepted, and the later occurrence
selects the view (`-1 -l` is details, `-l -1` is one-per-line). E0 tests both
orders; grid/tree view flags are rejected until E2/E1.

### 7.2 Operand model and display (B11/new 26)

- Zero operands means `.`. One operand is either a directory (list its
  contents) or a non-directory (list that one entry). `-d` treats a directory
  operand as a file entry.
- A directory operand that is a symlink to a directory is followed
  (`points_to_directory`), so its target contents are listed unless `-d`
  (verified source; probe confirms). An entry symlink is not followed for
  listing/tree.
- **Directory listings display entry names only.** Input operands display the
  parent prefix + name, matching eza's `FileName::paint`:
  - `eza file` → `file`;
  - `eza /tmp/file` → `/tmp/file`;
  - `eza ./file` → `./file`;
  - `eza -d /tmp/dir` → `/tmp/dir`;
  - `eza -d .` → `.`; `eza -d ..` → `..`; `eza -d /` → `/`.
- Implement `operandDisplay(p)` in eza from `paths::lastComponent`/
  `paths::parentOf`: if `p == "/"` return `"/"`; let `name = lastComponent(p)`,
  `parent = parentOf(p)`; `.`/`..` return themselves; if `parent == ""` return
  `name`; otherwise return `strings::parentPrefix(parent, outer)` + `name`
  (name quoted per §7.6). The shared `parentPrefix` root branch gives
  `/tmp/f`, not `//tmp/f`. This is lexical Rust `Path::components`
  normalization, not shell cleaning. Test the normalization table:
  `a//b`, `a/./b`, `dir/`, `./f`, `/tmp/f`, `/`, `.`, `..`, a spaced parent
  `x y/sub/f`, and a spaced `-d` operand `x y/sub/link` (P0 records the exact
  reference bytes; any double-slash/root spelling difference is resolved by the
  probe).
- Quoting of the parent and name follows section 7.6: the **whole parent
  string is escaped once** with the outer style, the name is escaped
  separately, and the `/` between them is added after the escaped parent (it
  is not quoted). `eza 'x y/sub/f'` prints `'x y/sub'/f`; `eza -d
  'x y/sub/link'` prints `'x y/sub/link'` (one quoted unit, no internal
  quote).
- **Committed goldens use relative, fixture-owned operands only.** Absolute
  operand display, `-d ..`, `-d /` and `-d .` are live/shape-only (section 9.4);
  their mode/mtime/path depend on the host/temp parent. `--record` refuses an
  absolute-looking stdout line as well as stderr.
- The operand is never hidden-filtered: `eza .hidden` lists it without `-a`.
- `-D`/`-f` are not applied to input/operand files (source:
  `filter_argument_files` applies only ignore patterns). They apply to
  directory children.
- Multiple operands are out of E0 and are rejected (section 7.11).
- Empty directory: no output, exit 0.
- Missing operand: error class in section 7.10; no stdout.

### 7.3 Hidden files, `-a`, `-A`, and synthesized dots (B4)

Count `-a`/`--all` occurrences; each `a` in a cluster counts. `-A`/`--almost-all`
is a boolean. The rule is verified from 0.23.3's `DotFilter::deduce`:

| count `-a` | `-A` | result |
|---|---|---|
| 0 | no | hidden names (leading `.`) excluded |
| 0 | yes | dotfiles included; no `.`/`..` |
| 1 | either | dotfiles included; no `.`/`..` |
| ≥2 | either | dotfiles included **and** `.`/`..` synthesized |

Consequences: `-a` alone shows `.hidden` but not `.`/`..`; `-aa` shows both;
`-A` shows `.hidden` but not dots; `-A -a` shows dotfiles but not dots;
`-A -aa` shows dots (c=2). `--all --all`, `-a -a`, `-aaa` all show dots.
`-A -A` is just `-A` (boolean).

`EZA_STRICT`/`EXA_STRICT` are pinned **unset** in every case. Under strict mode
eza treats duplicate/redundant flags (`-aaa`, `-A -A`, repeated `--sort`) as
errors; E0 implements only the default use-last mode and ignores strictness.
The environment pin and the divergence are documented; do not claim strict-mode
parity.

- Kernel `.`/`..` records are always discarded by `fs::next` (section 5.9).
- When dots are enabled, eza synthesizes them **before** the real entries, in
  the order `.`, `..`. Our eza builds the input vector in that order (two
  synthetic records) and then appends real entries in getdents order. This
  preserves eza's tie order for size/date sorts and its input order for
  `--sort=none` (out).
- Synthetic records: name `.`/`..`; path operand/`paths::join(operand,"..")`;
  type directory; stat for long/classify/grouping uses that path. The paths
  match eza's `new_aa`: `.` is `dir.path` (`new_aa_current`), `..` is
  `dir.path.join("..")`. The `.` `File.ext` key is **`File::ext(dir.path)`**:
  the extension of the path's final normal component, or `None` when
  `Path(operand).file_name()` is `None`. Concretely: `None` for an operand
  `.`, `..` or `/` (and any operand ending in `..`), `None` for a dotless
  operand `d`, `Some("x")` for `dx.x`, and `Some("")` for `dx.`
  (ASCII-lowercased after the last dot). Do **not** implement the shorthand
  `ezaExt(lastComponent(operand))` literally: `lastComponent(".")` is `"."`
  and `ezaExt(".")` would give `Some("")`, not `None`. `..`'s
  `file_name()` is `None`, so it has no extension.
- Synthetic dots are not hidden-filtered; real names beginning `.` are.
- `-aa` in an empty directory prints `.` and `..` (two lines, sorted).
- `-aa` with `-D` includes dots (directories); `-f` excludes them.
- `-aa --group-directories-first` groups dots with directories.
- `-aa --sort=extension`: `.`'s key is `File::ext(dir.path)` (§3.3) and
  `..` is `None`. With a **relative fixture-owned operand** the key is fixed
  by the case: `eza -aa --sort=extension d` (dotless) puts `.` in the `None`
  group, `eza -aa --sort=extension dx.x` puts it in the `Some("x")` group,
  and an operand `.` (or `..`, `/`) also puts it in the `None` group because
  the path has no `file_name()` (§3.3a). A name-only extension+`-aa` case may
  be committed when the operand is fixture-owned and every listed pair is
  comparator-distinct; otherwise it is live. The operand-`.` case is
  committed only when the case's cwd is the fixture directory (the listing
  displays names only) and the names are comparator-distinct.
- **Golden split:** `-aa` **name-only** lines are committed only when every
  listed pair (including the synthetic dot names) is comparator-distinct under
  the comparator used: `natcmpFold` for default/name and lowercase extension,
  `natcmp` for `Name`, `Ext`/`Extension` and type. eza's stable sort preserves
  `readdir` order for Equal keys, so case-only pairs (`A`/`a`,
  `README`/`readme`) make a **fold-order** golden host-dependent and are
  live-only there (under `natcmp` they are distinct and usable when every
  other pair is distinct), while whitespace-equivalent pairs (`pic4`/`pic 4`,
  `a`/` a`, and a real ` .` against the synthetic `.`) tie under both
  comparators and are live-only for every name-order golden. With that
  restriction, name/type/extension name-only `-aa` cases (names, relative
  fixture targets and the `.` key fixed by the fixture operand) may be
  committed. **`-aa --sort=size` and `-aa --sort=modified` are
  excluded from committed goldens**: eza's synthetic dots (`File::new_aa`) have lazy
  metadata, and the sort keys are the `st_size`/mtime of the listed directory
  and of the host temp parent, so the **order** is host-dependent even though
  only names are printed. They are live/shape-only unless the fixture directory
  and its parent are explicitly chmod/utime-pinned, their mode/mtime/`st_size`
  are recorded in a matching manifest, and the filesystem is the recorded one
  (§9.4). `-aa -l` is excluded for the same metadata reason: the `.` row is the
  listed directory and the `..` row the host temp parent.

### 7.4 Filtering `-D` and `-f`

- `-D/--only-dirs`: keep entries whose lstat type is directory (`is_directory`
  in eza). Symlinks to directories are **excluded** (they are links, not
  directories) unless `--follow-symlinks`, which is out.
- `-f/--only-files`: keep entries whose lstat type is a regular file.
  Symlinks (even to files), FIFOs and sockets are excluded.
- Both together: eza's match falls through to no filtering, so both flags
  cancel and all entries are shown. Match this; test.
- Type comes from `d_type` when known. For `DT_UNKNOWN`, eza's cached
  `DirEntry::file_type()` is `None` and `File::filetype()` lazily lstats, so
  resolve via lstat/mode; if lstat fails, the entry is unknown/special and is
  excluded by `-D`/`-f`. P0 probes a DT_UNKNOWN filesystem; normal fixtures
  have known types.
- Applied after hidden filtering and before sorting; operand files are
  exempt.
- `-D`/`-f` with `-d` operand: operand exempt; no listing.

### 7.5 Sorting (B5/new 22)

Input order is directory order after filtering, with synthetic dots first when
enabled. All sorts are stable ascending; `-r` reverses the whole order; group
directories first is a final stable sort.

| `--sort` | key | tie-break | notes |
|---|---|---|---|
| no `--sort` flag, `name`/`filename` | `natcmpFold(name)` | comparator-equal keys keep readdir order; committed cases need pairwise comparator-distinct names | Name(AaBbCc); `--sort=default` is **not** a value |
| `Name`/`Filename` | `natcmp(name)` | comparator-equal keys keep readdir order; committed cases need pairwise comparator-distinct names | case-sensitive natural |
| `size`/`filesize` | lstat `st_size` (`length()`) | none; equal keys keep readdir order (live-only) | directories use st_size for the key even though long displays `-`; exact goldens use regular files |
| `modified`/`date`/`time`/`mod`/`new`/`newest` | mtime | full sec+nsec; None first; equal keys keep readdir order (live-only) | eza `Option<NaiveDateTime>`; pre-1970 is None |
| `extension`/`ext` | `ezaExt(name)` lowercase, `None` before `Some` | `natcmpFold(name)`; equal name keys keep readdir order | primary ext compare is byte order of the lowercased ext; `Some("")` after `None` |
| `Ext`/`Extension` | same ext key | `natcmp(name)`; equal name keys keep readdir order | only the tie-break changes |
| `type` | type rank | `natcmp(name)` **case-sensitive**; equal name keys keep readdir order | rank: dir 0, regular 1, link 2, fifo 3, socket 4, char 5, block 6, special 7 |

**There is no `default` sort value.** `SortField::deduce` has no `"default"`
arm: `--sort=default` is
`eza: Option --sort (-s) has no "default" setting (choices: ...)`, exit 3. The
row above means the absent-flag default. `--sort=default` is an own bad-value
test (and matches eza's message when the reference is present).

**Global tie rule (all sorts).** eza calls `sort_by` (stable). When the
comparator returns Equal for two distinct entries, their relative order is
`readdir` order, which the filesystem chooses. Natord is not injective on
distinct names: `natcmpFold("A","a") = 0` and `natcmp("A","a") = -1`;
`natcmpFold("README","readme") = 0`; `natcmp("pic4","pic 4") = 0`;
`natcmp("a"," a") = 0`. A synthetic `.` also ties with a real name ` .`
(whitespace is skipped). Therefore a **committed** name/type/extension or
`-aa` golden requires every pair of listed names — including the synthetic dot
names — to be **comparator-distinct** under the comparator used (`natcmpFold`
for default/name and lowercase extension; `natcmp` for `Name`,
`Ext`/`Extension` and type). Any case with a comparator-equal pair is
live/shape-only; the live differential compares one fixture on
one host and is not a committed byte oracle. Size/date ties are live-only for
the same reason. There is no "names are unique so no tie-break" exception:
distinct names can compare Equal.

**Boundary cases.** Each pair is live-only **under the comparator where it
ties**. `A`/`a` and `README`/`readme` tie only under `natcmpFold`
(`compare_ignore_case`); under `natcmp` they are distinct (`A < a`,
`README < readme`), so a `Name`, `Ext`/`Extension` or `type` case may contain
both when every other pair is comparator-distinct. `pic4`/`pic 4`, `a`/` a`
and ` .`/`.` tie under both comparators because both skip whitespace, so they
are live-only for every name-order golden. Committed fixtures use pairwise
distinct names such as `A`, `B`, `a1`, `b2`, `pic3`, `pic5`; never combine a
committed `-aa` case with a real ` .`, and keep fold-order cases free of
case-only siblings.

- `ezaExt(name)`: substring after the last `.` in the name, ASCII-lowercased;
  no dot → `None`; name ending `.` → `Some("")`; `.git` → `Some("git")`. This
  is eza's `File::ext`, not `paths::ext`. For synthetic dots, `.` uses
  `File::ext(dir.path)` and `..` is `None` (section 7.3): the key is `None`
  when the operand path has no `file_name()` (`.`, `..`, `/`, a path ending
  in `..`), otherwise `ezaExt` of the final normal name; the operand
  component itself is **not** the key, and `ezaExt(".")`/`ezaExt("..")` are
  never applied to those operands.
- Extension sort implementation: pre-order by the name fallback, then stable
  sort by the ext string (empty for both None and Some("")), then stable sort
  by `hasExt` (0 None, 1 Some). This reproduces `Option<String>` ordering and
  keeps the name fallback within each ext. Test `a.txt`, `b.TXT`, `c`,
  `d.`, `.hidden`, `e.tar.gz`.
- Type sort implementation: pre-order case-sensitive natural by name, then
  stable sort by rank. `DT_UNKNOWN` resolves via lstat mode; if lstat fails,
  rank special (7) or the reference's choice (probe).
- Size/modified implementation: start from identity (directory order), stable
  sort by the key. For modified, stable sort by nsec first, then by sec, so
  sec is primary and nsec secondary. None (pre-1970) uses a `-1` sentinel
  before any real time.
- `-r`: `sort::reverse(order)` **after** the field sort. This reverses ties;
  exact fixtures avoid comparator ties for every sort (size/date and
  name/type/extension), and live differential handles them.
- `--group-directories-first`: after reverse, compute `dirRank` = 0 if
  `points_to_directory()` (follows symlinks; lstat fallback on error → 1) else
  1, and stable sort. Dirs stay first with `-r`; within-group order is the
  reversed order. `-aa` dots are dirs and group first.
- `--sort=none`, `.name`, `inode`, `changed`, `accessed`, `created`, `age`,
  `oldest` are out of E0; reject with an own usage error (section 7.11).
- `-t/--time` is a **timestamp field selector**, not a sort. E0 rejects it.
- Sorting never moves owned strings/records; it permutes int order arrays.
  Name and key arrays stay index-aligned. Key arrays may be freed after the
  order is computed, before output, to lower peak memory.

### 7.6 Quoting and escaping (B6)

Apply `strings::quoteName(s, quoteSpaces)` with the style named in each item:

- every name in default/`-1`/`-d` lines, long and later tree output;
- symlink target names, **always** with `QuoteSpaces`, even if an outer
  `--no-quotes` is added later;
- symlink target parent prefixes with the **outer** quote style: eza's
  `add_parent_bits` escapes `parent.to_string_lossy()` **once, as one string**,
  with `self.options.quote_style`, even though the target *name* uses
  `QuoteSpaces`. It has one root branch: a parent with one component that is
  `/` emits a single `/` and appends no second separator. Use the shared
  `parentPrefix` primitive (§5.2): `parentPrefix("/", outer)` is `/`, so a
  target `/tmp` displays `/tmp`, not `//tmp`;
- the parent prefix of an input operand uses the **same** `parentPrefix`
  primitive: escape the whole parent string once with the outer style; the `/`
  between parent and name is added after the escaped parent and is never inside
  the quotes. Do **not** quote per path component: `x y/sub/f` is
  `'x y/sub'/f`, not `'x y'/sub/f`, and `/tmp/f` is `/tmp/f`, not `//tmp/f`;
- broken symlink target paths (raw target string, escaped with the outer
  style).

Rule summary (section 5.1.2): quote if the name contains space or apostrophe;
double quotes if it contains an apostrophe, else single; controls escaped with
`\t`, `\n`, `\r`, `\u{...}`; embedded quotes not escaped. Sorting uses the raw
(lossy in eza) name, never the quoted form. Classify markers are appended
**after** the closing quote.

**Symlink target rendering (lines/default/`-1`/`-d` and long/tree):** a valid
target is ` -> ` + parent prefix (outer style) + target name
(`QuoteSpaces`); a broken target is ` -> ` + the raw target path escaped with
the outer style; an error target (readlink/metadata failure) shows no arrow or
target. The link name itself is escaped with the outer style, then the arrow
and target follow. The grid view (E2) is the only view that omits target paths.

Exact fixture names (ASCII/valid UTF-8): `plain`, `a b`, `it's`, `a b'c`,
`a"b c`, `a"b'c d`, `back\slash`, `tab\there`, `line\nfeed`, `esc\x1b`,
`del\x7f`, `sp ace\x1b`, `-dash`, `--two`, `255-byte name`; symlink targets
`target`, `dir`, `prog`, `tar get`, `broken`, `../tar get`, `a b/target`, and a
target with a control byte. **Spaced-parent cases:** operand `x y/sub/f` →
`'x y/sub'/f`; `-d 'x y/sub/link'` → `'x y/sub'/link`; operand `x y/sub/link`
that is a symlink to `target` → `'x y/sub'/link -> target`; entry
`link -> a b/target` → `link -> 'a b'/target` (target parent whole-escaped,
target name `QuoteSpaces`). No invalid UTF-8 in E0 exact cases.

**Absolute-target live cases** (live/shape-only; never committed, §9.4):
`abstmp -> /tmp` without `-F`, `abstmp -> /tmp/` with `-F=always`,
`absroot -> /` without `-F`, `absroot -> //` with `-F=always` (target `/`
plus the directory marker). These exercise the parent-prefix root branch; P0
records the exact bytes.

**`/tar get` is a broken target, not a root-parent case.** A normal test
cannot create a file at the filesystem root named `tar get`, so a symlink to
`/tar get` is Broken: eza displays `spaceabs -> '/tar get'` with no marker,
even under `-F=always` (the broken branch escapes the whole raw target with
the outer style and never appends a marker). It is kept as a **broken-target
live case** only; do not expect `/'tar get'` or `/'tar get'/`.

**`../tar get` is also a broken target.** The fixture owns the case directory
only, creates no `tar get` sibling in the case directory's parent, and asserts
that no such entry exists there, so the target metadata lookup fails and the
whole raw target is escaped with the outer style: `link -> '../tar get'`, with
no marker even under `-F=always`.
The design creates no host path outside the case/fixture directory (no
`/tmp/x y/target`, no parent sibling), so it never claims a valid
`../tar get`. Valid whole-parent quoting for a spaced parent is covered by
the relative target `a b/target` (the fixture creates directory `a b` and file
`target` inside the case directory; → `link -> 'a b'/target`); broken
whole-raw quoting is covered by `a b/missing` (→ `'a b/missing'`) and by
`../tar get` (→ `'../tar get'`).

### 7.7 `-F/--classify` (B7)

- No `-F`: no markers.
- `-F` with no value: value `auto`; in E0 stdout is not a TTY, so **no
  markers**. (E0 has no `isatty`; do not pretend to detect a TTY. On a TTY E0
  diverges until E1.)
- `-F=always`, `-Falways`, `-F always`, `--classify=always`: markers.
- `-F=auto`, `-Fauto`, `-F auto`, `--classify=auto`: auto (no markers in E0).
- `-F=automatic` is accepted (the `=` form bypasses the consume-list check and
  the option deduction accepts `automatic`); `-Fautomatic` is a forbidden-value
  error, as is `-Fl` (section 5.6).
- `-F=never`, `--classify=never`: no markers.
- `--classify=automatic`: accepted after `=` and means auto. `--classify
  automatic` does **not** consume `automatic` (the parser's consume list is
  `always|auto|never`); `automatic` becomes an operand and is then a metadata
  error. Match this exactly.
- `-F` in a cluster at the end uses auto; `-FlFalways` etc. per the parser
  rules in section 5.6. `-Fl` is a forbidden-value error.
- Markers (verified source): regular file with user-execute bit `*`;
  directory `/`; FIFO `|`; symlink `@`; socket `=`; nothing for char/block
  devices. The executable test is regular **and** `mode & 0o100`, not any
  execute bit.
- **Marker placement (the v2 bug):**
  - **Lines/default/`-1`/`-d`:** eza calls `with_link_paths()`, so a valid
    target is shown even without `-F`; with `--classify=always` the marker is
    on the **target** (`link -> target/`, `link -> prog*`, target `|`/`=` as
    applicable). Broken/error targets get no marker. A non-symlink gets the
    marker on its name (`file*`, `dir/`, `fifo|`, `sock=`).
  - **Long/tree:** the target is shown; the marker is on the **target**, not
    the link; broken/error targets get no marker.
  - **Grid (E2):** target paths are omitted; the marker is on the **link**
    (`link@`). This is the only view where the link carries the marker.
- Valid/broken classification uses **stat (follow)** on the resolved target
  path (§7.9), not lstat of the link or of the raw target string. Consequences:
  - an entry `link -> b`, where `b` is itself a symlink to a directory, gets
    `/` (followed through the chain), not `@`;
  - a chain/loop `a -> b -> a` fails stat with ELOOP → **Broken**, so no
    marker (an lstat of `b` would succeed and wrongly add `@`);
  - `-d sub/link -> target` resolves `sub/target`; a cwd-relative stat of
    `target` or `sub/link/target` gives the wrong marker and broken decision.
- If the name/target is quoted, the marker comes after the closing quote:
  `'a b'*`, `link -> 'tar get'/`.
- **`--icons` bare semantics:** no `--icons` → no icons (ShowIcons::Never,
  unless `EZA_ICONS_AUTO`, which is pinned unset). Bare `--icons`,
  `--icons=auto` and `--icons=automatic` mean automatic; in E0 (non-TTY) that
  is **no icons**, so accept them as no-ops. `--icons=never` is no icons.
  `--icons=always` is an own usage error (E0 has no icon rendering); E1 may
  add icons. `--icons automatic` does not consume `automatic` (consume list is
  `always|auto|never`), so `automatic` becomes an operand.
- Exact cases to add: default/`-1` symlink to file, to dir, to executable,
  target with spaces, broken (including `../tar get`, no sibling, no marker
  even with `-F=always`), target error; `-F=always` lines for the same;
  `-d` symlink lines including **`-d sub/link` to an executable
  target (`*`) and to a directory target (`/`)**; an entry whose target is
  itself a symlink to a directory (marker `/`); a **chain/loop `a -> b -> a`
  with `-F=always`** (no marker); spaced parent `x y/sub/link`; mixed directory
  lines; no `-F` (targets still shown, no markers); grid marker-on-link cases
  deferred to E2.

### 7.8 Restricted long mode (B10/new 21/23)

Required invocation:

```
eza -l --bytes --time-style=long-iso (-n | --no-user) [other E0 flags] [operand]
```

- `-l` without `--bytes`, without `--time-style=long-iso`, or without `-n` **or**
  `--no-user` is an **own usage error** (exit 3, own golden). Both `-n` and
  `--no-user` may be given; `--no-user` wins (no user column). The requirement
  applies only when `-l` is the effective (last) view flag: `-l -1` is
  one-per-line and needs no long flags; `-1 -l` is long and does.
- `--time-style`: when `-l` is the effective view, the value must be `long-iso`
  (or `long-iso` as a separate token); other styles are own usage errors. When
  `-l` is not effective, `long-iso` and `default` are accepted and ignored
  (there is no date column) and other values are own usage errors. Eza accepts
  and ignores any valid style with `-1`; our narrower non-long surface is a
  documented divergence, tested with `--time-style=default -1` (accepted, no
  date) and `--time-style=iso -1` (own error).
- `-B` is accepted as the short form of `--bytes`.
- `-H/--links`, `-g/--group`, `-i/--inode`, `-S/--blocksize`, `-m/-u/-U/-c`,
  `--no-permissions`, `--no-filesize`, `--no-time`, `--header`, `-b/--binary`
  are unsupported in E0 (reject; E1 may add selected columns).

**Column set and order (verified from 0.23.3):** permissions, size, user (if
`-n` and not `--no-user`), modified date, name. No links column, no group
column, no inode/blocks, no header, no totals.

**Rendering grammar:**

```
line := perms " " sizePad " " [ userPad " " ] datePad " " name [" -> " target] [marker]
```

- `perms`: 10 chars via `fmt::modeString` (eza type chars; section 5.4). With
  xattrs eza appends `@` (11 chars). E0 does not query xattrs; fixtures have
  none. If a fixture has an xattr, the reference may differ; document and keep
  such fixtures out.
- `size`: for a regular file, decimal `st_size`; for directory, symlink, FIFO,
  socket, other non-device: `-` (one char). Char/block devices show
  `major,minor` in eza; E0 renders `-` and fixtures avoid device nodes.
  Right-aligned to `Ws = max display width of the size cells in the listing`.
  Under `LC_ALL=C`, `--bytes` prints plain digits (the C locale's thousands
  separator is empty); P0 records a 7-digit size. If the probe shows grouping,
  add `fmt::groupDecimal` and use it; exact size fixtures stay <1000 except the
  recorded case.
- `user`: numeric uid decimal, **left-aligned** to
  `Wu = max digits of the uid cells` (`-n`). Omitted entirely with
  `--no-user`. If both `-n` and `--no-user`, omitted.
- `date`: `YYYY-MM-DD HH:MM` in UTC (`TZ=UTC` pinned), or `-` for a negative/
  unrepresentable mtime; left-aligned to `Wd = max width of the date cells`
  (16 when any modern entry is present).
- `name`: quoted/escaped per section 7.6. Symlink: append ` -> `, the target
  parent prefix via `parentPrefix` with the outer style, and the target name
  quoted with `QuoteSpaces`; broken target: append ` -> ` and the escaped raw
  target. Classify marker per section 7.7.
- Each table cell is followed by exactly one space (eza's table render), then
  the name. A `-` cell occupies width 1 before padding.
- Widths are computed over the displayed entries after filtering and sorting.
  For `-d`/single operand, over that one entry. No truncation to terminal
  width; long output is not wrapped.
- Implementation: one lstat pass fills parallel `[]int` arrays for mode, size
  (or `-1`), mtime seconds, mtime nanoseconds and uid; widths are maxima over
  those arrays; rendering reads the arrays in sorted order. Free sort-key
  arrays after the order is computed and before output to lower the peak. This
  is the memory shape recorded by the 10,000-entry case.
- Long `-d`: describe the operand itself (mode/size/user/date/name), not its
  contents. Committed long `-d` goldens use a relative fixture-owned file,
  directory or symlink; `-d .`, `-d ..`, `-d /` and absolute operands are
  live/shape-only.
- Long `-aa`: two extra directory rows. **Committed long goldens exclude
  `-aa -l`**: `.` shows the listed directory's mode/mtime and `..` the host
  temp parent's. They are live/shape-only unless the directory and its parent
  are explicitly chmod/utime-pinned and both are recorded in the manifest
  (section 9.4). The dot extension quirk does not affect size/modified keys.
- Pre-1970 mtime: date cell `-`; the `time` module still computes correctly for
  future use.
- Permission examples: `.rw-r--r--`, `drwxr-xr-x`, `lrwxrwxrwx`,
  `|rw-r--r--`, `srwxr-xr-x`, `.rwsr-xr-x`, `.rwxr-sr-x`, `drwxrwxrwt`,
  `.rw-r--r-T`.
- **Worked example** (design target; P0 confirms exact bytes). Directory with
  regular `a` size 0 mode 0644, regular `b` size 1234, directory `d`, symlink
  `l -> target`, all uid 1000, mtime 2020-01-02 03:04:

  ```
  .rw-r--r--    0 1000 2020-01-02 03:04 a
  .rw-r--r-- 1234 1000 2020-01-02 03:04 b
  drwxr-xr-x    - 1000 2020-01-02 03:04 d
  lrwxrwxrwx    - 1000 2020-01-02 03:04 l -> target
  ```

  With `--no-user`, the uid cells and one separator disappear:

  ```
  .rw-r--r--    0 2020-01-02 03:04 a
  .rw-r--r-- 1234 2020-01-02 03:04 b
  drwxr-xr-x    - 2020-01-02 03:04 d
  lrwxrwxrwx    - 2020-01-02 03:04 l -> target
  ```

- `--classify=always` appends markers: `a` (mode 0644) has none; an executable
  regular file gets `*`; `d/`; `l -> target/` if `target` is a directory.
- **Divergence:** plain `eza -l` (names, human sizes, default time) is not
  supported; our tool errors. This is the accepted user-visible restriction.
  User/group names and local time are out of E0/E1.

### 7.9 Symlinks and entry paths

- Every entry uses **lstat** for its own mode/size/mtime/uid, so a symlink
  shows its own metadata; `readlink` gives the raw target bytes.
- **Operand existence is checked with lstat, not stat.** eza's
  `File::metadata` uses `symlink_metadata`; a dangling symlink operand
  therefore exists and is listed as `link -> target` (Broken, no marker), not
  an input-metadata failure/exit 2. The directory-vs-file operand decision
  still follows (`points_to_directory`) for a symlink to a directory, and `-d`
  keeps it as one entry. Test a dangling operand with and without `-d`.
- **Target path resolution** (the E0 rule, from eza's `reorient_target_path`).
  Let `raw` be the bytes `readlink` returned and `entryPath` the path used to
  stat/readlink the link:
  - absolute `raw` (starts with `/`): the target path is `raw` exactly;
  - a **directory entry** `name` in listing `dir`: `targetPath =
    paths::join(dir.path, raw)` (equivalently
    `paths::join(parentOf(entryPath), raw)`);
  - a **`-d` operand** `sub/link`: `targetPath =
    paths::join(parentOf(operand), raw)` — `sub/target` for `raw=target`,
    **not** cwd `target` and **not** `sub/link/target`; when
    `parentOf(operand)` is `""`, `targetPath = raw`;
  - a non-`-d` operand that is a symlink to a file is displayed as one entry and
    uses the same rule with the operand's parent as the base (a symlink operand
    to a directory is followed for listing unless `-d`, §7.2, and is not
    displayed as an entry).
  **Display uses `raw`, not `targetPath`:** target name = last component of
  `raw`, target parent prefix = `parentPrefix(parentOf(raw), outer)`
  (§5.2/§7.6; a root parent emits a single `/` and no second separator),
  target name always `QuoteSpaces`. A broken target displays the whole raw
  target string escaped with the outer style (`../tar get`, with no sibling
  in the case directory's parent, → `'../tar get'`). Absolute targets are
  live/shape-only: `/tmp` → `/tmp` (`/tmp/` with `-F=always`), `/` → `/`
  (`//` with `-F=always`, target plus directory marker). A raw target
  `/tar get` is Broken, not a valid target: a normal test cannot create it,
  so it displays `link -> '/tar get'` with no marker even under `-F=always`,
  and it does not exercise the root-parent prefix.
- **Follow semantics.** Classification and broken detection call **stat
  (follow)** on `targetPath` — eza's `link_target` uses `std::fs::metadata`,
  which follows. `Ok` → the followed file type drives the classify marker
  (directory `/`, executable `*`, FIFO `|`, socket `=`, etc.). Any stat error
  (ENOENT, ELOOP, EACCES, ENOTDIR) → **Broken**: raw target shown, **no
  marker**. A `readlink` error → **Err**: no arrow and no target. The link's
  own long cells (permissions, size `-`, date, uid) remain from lstat.
- **Consequences to test:** `-d sub/link -> target` with executable
  `sub/target` shows `sub/link -> target*`; `-d sub/link -> dir` shows
  `sub/link -> dir/`; an entry `link -> b` whose `b` is itself a symlink to a
  directory is `Ok` and gets `/`; a chain/loop `a -> b -> a` is `Broken` and
  gets no marker even with `-F=always`; a target with `../`, `./` or multiple
  components is classified through the resolved path, never by the display
  string.
- **Lines/default/`-1`/`-d` and long/tree always show valid targets; the grid
  view (E2) does not.** E0 exact targets use relative fixture-owned names plus
  a few `../`/multi-component shape cases; absolute targets are live/shape-only
  (§9.4).
- Entry symlinks are never followed for listing/tree descent. A symlink to a
  directory is still a link entry; `points_to_directory` (follow) is used only
  for the operand decision and group-directories-first. `-D` uses
  `is_directory` (lstat) and excludes symlink dirs; group-dirs-first uses
  `points_to_directory` (follow) and includes them.
- Symlink loops are just link entries; no recursion follows them. Bind-mount
  cycles are out; `-L` bounds tree depth in E1; no visited set in E0.
- `readlink` on a dangling link succeeds; the target's metadata is missing and
  the target is Broken. A non-symlink `readlink` is EINVAL (not reachable from
  a lstat-link entry). Other readlink errors are permission/EIO; an error
  target shows no arrow (probed).
- Entry path for stat/readlink is `entryPath(operand,name)`; for tree children
  it is the child Dir's path. Tests use operands outside cwd (`/tmp/.../dir`)
  and relative `../dir`, and assert the link's lstat fields match `os.lstat` on
  the joined path and the target classification matches
  `os.stat(targetPath)`.
- Joined path ≥4096: `ENAMETOOLONG`; documented limitation and future
  `fstatat`/`readlinkat` fix (section 6.7).

### 7.10 Diagnostics and exit codes (B9)

Exit contract (constants in `errors`; probe every class):

| class | status | stderr shape |
|---|---|---|
| success | 0 | empty |
| input path metadata failure (missing/unstatable operand; the check is **lstat**) | 2 | `"path": No such file or directory (os error 2)` (no `eza:` prefix; debug-quoted path). A **dangling symlink operand is not this class**: lstat succeeds, so it is listed as `link -> target` (Broken). |
| directory read error, not permission | unchanged (0 unless another error set 2) | `<path>: <io error>`; continue |
| directory permission denied | probe: source prints but does not assign 13; the man page documents 13. Record actual. | `Permission denied: <path> - code: 13`, then a blank line, `Skipped N directories due to permission denied: `, and `  <path>` lines |
| write error | 1 | `<io error>` (no `eza:` prefix), e.g. `No space left on device (os error 28)` |
| option error (unknown/missing/forbidden/bad value) | 3 | `eza: <error>` |
| our restricted `-l`/unsupported option | 3 | own message (own golden) |
| broken pipe | SIGPIPE default (shell 141); if observed as an error, eza exits 0 | probe |

- Match eza's option wording where the reference has it:
  `eza: Unknown argument --foo`, `eza: Unknown argument -Q`,
  `eza: Flag --sort needs a value (choices: name, Name, size, extension, Extension, modified, changed, accessed, created, inode, type, none)`,
  `eza: Flag --all cannot take a value`, `eza: Option --sort (-s) has no "foo" setting (choices: ...)`.
  For **optional** flags there is no choices text:
  `eza: Option --classify (-F) has no "foo" setting` (also `--color`,
  `--icons`); `-Ffoo`/`-Fl`/`-F=` are `eza: Flag -F cannot take a value`.
  Equals splitting is `split_on_equals` for both forms (non-empty name before
  the `=`, at least one byte after it; §5.6): `--classify=`/`--sort=`/
  `--all=` are `eza: Unknown argument --classify=`/`--sort=`/`--all=`;
  `--classify==` is `eza: Option --classify (-F) has no "=" setting`;
  `-s=` is `eza: Option --sort (-s) has no "=" setting (choices: ...)`;
  `-a=` is `eza: Unknown argument -=`; `--=x` is
  `eza: Unknown argument --=x`; `--foo=bar`/`--foo==` are
  `eza: Unknown argument --foo`; `--foo=` is
  `eza: Unknown argument --foo=`; `-Qfoo`/`-Q=foo` are
  `eza: Unknown argument -Q` (first unknown short byte); `-I=` takes the
  value `"="` (then E0 rejects `-I` as unsupported); `-lF=always` is
  accepted.
  `cli` returns the message without the `eza: ` prefix; eza prefixes it.
- Input metadata errors use `errors::debugPath` + `": "` + `errors::osError`.
  The operand existence check is **lstat** (eza's `symlink_metadata`), so a
  dangling symlink operand is listed, not an exit-2 error. Exact stderr cases
  use simple ASCII names; path quoting/control cases are shape-compared or
  probed.
- Directory permission cases are skipped under root (fixtures unreadable only
  as non-root). Do not run a mixed-user matrix.
- Write error: test `/dev/full` only if stable (eza may print a different
  shape); own golden/shape compare.
- `exit` skips defers/leaks; flush stdout before exit. The memory gate runs
  success paths so leaks are observable.
- `--help`/`--version` are unsupported in E0 and are **not in the E0 parser**
  (§5.6/§7.11): they take the ordinary unknown-name path
  (`eza: Unknown argument --help`/`--version`), exit 3, not eza's
  help/version text. `--help=x`/`--version=x` split to the name
  `--help`/`--version` and are `eza: Unknown argument --help`/`--version`, not
  a `cannot take a value` error.

### 7.11 Flag matrix (eza 0.23.3 short and long)

Legend: **S** supported E0; **L** later (E1/E2); **R** rejected in E0 with an
own usage error (exit 3). Rejection tests are own goldens; unknown-to-eza forms
match eza's message when probed.

| Flag | Long | E0 |
|---|---|---|
| `-1` | `--oneline` | S (one entry per line, symlink targets shown; no-op on non-TTY default) |
| `-l` | `--long` | S restricted (section 7.8) |
| `-a` | `--all` | S (count) |
| `-A` | `--almost-all` | S (boolean) |
| `-d` | `--treat-dirs-as-files`, `--list-dirs` alias | S |
| `-r` | `--reverse` | S |
| `-s` | `--sort` | S values: `name`/`filename`, `Name`/`Filename`, `size`/`filesize`, `modified`/`date`/`time`/`mod`/`new`/`newest`, `ext`/`extension`, `Ext`/`Extension`, `type`. The absent flag is the default (`Name(AaBbCc)`); **`default` is not a value** and `--sort=default` is rejected with eza's bad-value message. R values: `.name`/`.filename`/`.Name`/`.Filename`, `inode`, `changed`/`ch`, `accessed`/`acc`, `created`/`cr`, `age`/`old`/`oldest`, `none` |
| | `--group-directories-first` | S |
| `-F` | `--classify` (optional WHEN) | S (`auto`/`always`/`never`; `automatic` after `=`) |
| `-D` | `--only-dirs` | S |
| `-f` | `--only-files` | S |
| `-B` | `--bytes` | S (required by `-l`) |
| `-n` | `--numeric` | S (required by `-l` unless `--no-user`) |
| | `--no-user` | S (needed for goldens) |
| | `--time-style` | S `long-iso` when `-l` is effective; with a non-long view, `long-iso`/`default` are S and ignored, other values R |
| | `--color`, `--colour` (optional WHEN) | S **bare `--color`/`--colour`** (auto; non-TTY E0 means no colour), `--color=auto`, `--color=automatic` (after `=`), `--color=never`; R `always` until E1 |
| | `--icons` (optional WHEN) | S bare/`auto`/`automatic`/`never` as no-icons (E0 non-TTY); R `always` until E1 |
| `-?` | `--help` | R (not in the E0 parser: unknown-name error) |
| `-v` | `--version` | R (not in the E0 parser: unknown-name error) |
| `-G` | `--grid` | L E2 |
| `-x` | `--across` | L E2 |
| `-R` | `--recurse` | R |
| `-T` | `--tree` | L E1 |
| `-L` | `--level` | L E1 |
| `-X` | `--dereference` | R |
| | `--follow-symlinks` | R |
| `-w` | `--width` | L E2 |
| | `--no-quotes` | L E1 |
| | `--absolute` | R |
| | `--color-scale`, `--colour-scale` | R |
| | `--color-scale-mode`, `--colour-scale-mode` | R |
| `-I` | `--ignore-glob` | R |
| | `--git-ignore` | R |
| | `--group-directories-last` | R |
| | `--no-symlinks` | R |
| | `--show-symlinks` | R |
| `-b` | `--binary` | R |
| `-g` | `--group` | R (E1 may add) |
| `-h` | `--header` | R |
| `-i` | `--inode` | R |
| `-H` | `--links` | R E0; E1 optional |
| `-m` | `--modified` | R |
| | `--changed` | R |
| `-S` | `--blocksize` | R |
| | `--total-size` | R |
| `-t` | `--time` (field selector) | R (not a sort) |
| `-u` | `--accessed` | R |
| `-U` | `--created` | R |
| | `--hyperlink` | R |
| `-M` | `--mounts` | R |
| | `--smart-group` | R |
| | `--no-permissions` | R |
| | `--no-filesize` | R |
| | `--no-time` | R |
| | `--git`, `--no-git`, `--git-repos`, `--git-repos-no-status` | R |
| `-@` | `--extended` | R |
| `-o` | `--octal-permissions` | R |
| `-Z` | `--context` | R |
| | `--stdin` | R |
| `-O` | `--flags` | R |
| | `--` | S (end options) |

- `--sort=default` is **not** one of eza's choices; the absent flag is the
  default. It is a bad-value test with eza's exact
  `Option --sort (-s) has no "default" setting (choices: ...)`, exit 3.
- Options are parsed after free operands until `--` (`d -a` works). Equals
  splitting is `split_on_equals` for short and long tokens: split only when
  the name before the first `=` is non-empty and at least one byte follows it
  (§5.6). Thus `--classify=`/`--sort=`/`--all=` are `Unknown argument ...=`,
  `--classify==` is a `"="` bad value, `-s=` takes `"="` as the required sort
  value (bad value), `-I=` takes `"="`, `-a=` is `Unknown argument -=`,
  `-F=` is `Flag -F cannot take a value`, and `--=x` is
  `Unknown argument --=x`; a mixed cluster with `=` works (`-lF=always`).
- `-o` in eza 0.23.3 is **`--octal-permissions`**, not `--no-user`; do not
  implement a GNU-ls `-o` meaning.
- `--list-dirs` is an accepted alias of `--treat-dirs-as-files` in 0.23.3;
  canonical spelling in messages/docs is `--treat-dirs-as-files`.
- Unsupported but eza-valid options in E0 use our own message, e.g.
  `eza: option '--grid' is not supported by this build`, exit 3. This is a
  deliberate divergence; do not silently ignore. Rejected eza-valid flags
  are still parsed with eza's value mode before the unsupported-option error,
  so their values are consumed (`--grid` none, `--width=N` required,
  `-I=` required value `"="`, `--ignore-glob=foo` value `foo`,
  `--time-style=iso` value `iso`); otherwise a rejected flag's value would
  become an operand and change the error class.
- `--help`/`--version` (`-?`/`-v`) are **excluded from this rule and from the
  E0 parser** (§5.6): they have no `FlagSpec`, so they take the ordinary
  unknown-name path (`eza: Unknown argument --help`/`--version`/`-?`/`-v`),
  exit 3. `--help=x`/`--version=x` split (non-empty name, non-empty value) and
  are `eza: Unknown argument --help`/`--version`, not eza's ForbiddenValue
  messages (`Flag --help cannot take a value`,
  `Flag --version cannot take a value`). E0 prints neither help nor version
  text. P0 records eza's own help/version output for `REFERENCE.md` only; it
  is not an E0 golden.
- Unknown-to-eza options match eza's `Unknown argument` wording (probe exact).
- A lone `-` is an operand; names beginning `-` need `--`.
- Multiple operands: own usage error
  `eza: this build supports at most one operand`, exit 3.
- `--sort=none`: own usage error (filesystem order is not a stable oracle).

### 7.12 Out of scope and documented divergences

Out of E0/E1 unless a later phase says otherwise:

- user/group **names**, NSS/passwd/group; human-readable sizes (`-b`, `-S`,
  default prefixes); local timezones and the default/relative time styles;
- icon rendering (`--icons=always`; bare/`auto`/`automatic` are no-icon no-ops
  in non-TTY E0), git status, hyperlinks, mounts, `--total-size`;
- multiple operands and per-directory headers; `-R` recursion;
- `--sort=none`, inode/blocks/accessed/created/changed sorting;
- `--help`, `--version`, option abbreviation, `--no-` inference;
- full `LS_COLORS`/`EZA_COLORS`, `ln=target`, long-column colour (until
  probed); full Unicode width; invalid-UTF-8 display (E2);
- device major/minor rendering; ACLs/xattrs/SELinux/capabilities;
- floats, concurrency, parallel directory walks, caches;
- Windows/macOS/other architectures.

Documented divergences:

1. **Non-TTY only**: on a TTY E0 prints one per line with no colour until E2,
   where eza grids and colours by default.
2. **Restricted long**: plain `eza -l` is an error; only the explicit
   `--bytes --time-style=long-iso` mode with numeric/no user is supported.
3. **UTC**: dates are UTC; eza uses local time. `TZ=UTC` is required for
   parity.
4. **No user/group names, human sizes, device nodes, xattrs**.
5. **Invalid UTF-8**: eza lossy-decodes to U+FFFD and sorts/prefixes the lossy
   string; E0 outputs raw bytes and excludes such names from exact cases.
   E2 implements lossy display.
6. **Long joined paths** ≥4096 fail with `ENAMETOOLONG` where eza's dirfd
   metadata may succeed.
7. **Path race** between getdents and stat/readlink; tests do not mutate.
8. **Permission status**: the man page says 13; the 0.23.3 source's directory
   branch continues. The probe decides the required status; our tool matches
   the probe, not the man page, and records the discrepancy.
9. **Empty operand**: `cpath` rejects an empty path with `EINVAL`; eza's OS
   call would report `ENOENT`. Exact cases exclude the empty operand; it is a
   shape/own case. Embedded NUL cannot occur in argv.
10. **`--time-style` with a non-long view**: eza accepts and ignores any valid
    style with `-1`; this build accepts only `long-iso`/`default` and rejects
    the rest. Long mode supports only `long-iso` (item 2).

---

## 8. E1 and E2

### 8.1 E1 colour (P5b)

- Scope: `--color/--colour=auto|always|never` (optional WHEN), default palette
  for the fixture types, `NO_COLOR`, and a documented `LS_COLORS`/`EZA_COLORS`
  subset. Long-column colour and `ln=target` remain out until the reference's
  per-column styling is probed and recorded.
- `NO_COLOR` precedence (verified source): `NO_COLOR` present sets the
  **default** to `Never`; an explicit `--color` overrides it. Thus
  `NO_COLOR=1 eza --color=auto` is automatic (colours on a TTY), and
  `NO_COLOR=1 eza --color=never` is never. Do not invent "NO_COLOR always
  wins".
- `auto` needs `isatty(stdout())` (P5a slot 37). E1's non-TTY colour tests use
  `--color=always`; TTY tests use a pty or a pinned width with the
  `--width` caveat (section 8.3).
- Default palette: under a cleared colour environment, record the reference's
  SGR sequences for directory, symlink, executable, regular, FIFO, socket,
  hidden, broken symlink and setuid/setgid/sticky. The exact codes are probes,
  not guesses.
- `LS_COLORS`/`EZA_COLORS` subset: keys `di`, `ln`, `ex`, `fi`, `*.EXT`
  (longest-suffix/order per probe); SGR parameter strings; missing/empty resets
  to the class default; unknown keys/non-SGR values ignored or reference
  behaviour; `EZA_COLORS` overrides `LS_COLORS` (source) and `EXA_COLORS` is a
  fallback; advanced keys out.
- ANSI bytes are never counted in width. Build the plain name, compute layout,
  then wrap. Fixtures avoid ESC/control bytes for colour cases.
- Long colour: probe whether eza colours columns; if yes, defer it; colour
  differential uses non-long listings until implemented.

### 8.2 E1 tree (P5b)

- `-T/--tree`, `-L/--level`; tree is a details view (probe whether `-T`
  without `-l` prints columns or names only).
- Glyphs/indentation are the reference's bytes (likely UTF-8 `├──`, `└──`,
  `│`); record with a probe. ASCII fallback only if the reference has one.
- Sorting/filtering/hidden rules apply at each level. Synthetic dots are not
  descended (`is_all_all`); entry symlinks are not descended unless
  `--follow-symlinks` (out); `-L` bounds depth.
- Empty dirs print their line (or the reference marker); directory-open errors
  print and continue where the reference continues.
- Tree + two `-a` is an error in eza (`Option --tree is useless given --all
  --all`); own/ref golden.
- Tree + `-d` conflict (source `DirAction`): own/ref golden.
- Tree does not need terminal width; it can land before grid.
- `-L` without `-T` is an error in strict mode; E1 follows the probe.

### 8.3 E2 grid (P6)

- TTY grid is eza's default mode; `-1/--oneline` disables it. E2 implements
  the grid only on a TTY or with `--width`.
- `--width=COLS` sets `TerminalWidth::Set`; `actual_terminal_width()` becomes
  `Some(COLS)`, so eza treats redirected output as terminal-ish: it grids and
  enables `auto` colour/classify/icons. E2 must reproduce this interaction,
  not just the layout. E0 rejects `--width` so this cannot leak into E0 tests.
- Width source: `--width`, else `COLUMNS`, else ioctl `TIOCGWINSZ` on stdout
  (`terminalWidth`, P6a slot 39), else 0/unknown → fallback 80. `COLUMNS=0`
  and invalid values follow the probe.
- **Grid symlinks:** eza's grid view does not show target paths; the classify
  marker (when enabled) is on the **link** (`link@`). This is the E2 test for
  the marker-placement distinction; lines/default and long/tree mark the
  target (E0).
- Layout: column widths, gap width, down-then-across vs across (`-x/--across`),
  truncation/overflow, minimum width; all probes. Use `--width` and a pty to
  pin. Grid tests are ASCII first.
- Because `--width` makes eza's `auto` modes terminal-ish, grid cases must pass
  `--icons=never` (icons remain out of scope) and set the colour mode
  explicitly (`--color=never`, or the E1 colour case's value). Otherwise the
  reference would add icons/colour that our tool does not implement in E2.
- `-x/--across` is E2 (otherwise rejected).
- If pty behaviour is unstable in CI, use `--width` as the primary grid
  oracle and keep pty tests optional.

### 8.4 E2 UTF-8 and non-UTF-8 (new 20)

- `strings::displayWidth` gains UTF-8 decode: validate continuation bytes;
  width 1 for ordinary code points, 0 for combining/zero-width, 2 for East
  Asian wide; invalid bytes width 1 or the reference's replacement behaviour.
  The width table must be generated/documented; full parity with eza's exact
  `unicode-width` crate version is a stretch goal.
- Sorting: `natcmp`/`natcmpFold` decode UTF-8 and compare code points; fold
  is simple Unicode lowercase, not full case folding (documented divergence
  from eza's `to_lowercase` multi-char expansions). Curated tests: ASCII,
  `é`, `中`, an emoji, a combining sequence, a lone continuation byte.
- Display: eza lossy-decodes invalid UTF-8 to U+FFFD before quoting and
  sorting. E2 implements lossy conversion for display/sort; raw-byte output is
  the E0-only divergence. Until E2, non-UTF-8 names are absent from exact
  cases.
- Grid width uses `displayWidth`, not `len`.
- Non-UTF-8 names in long/default output are still listed (lossy) once E2
  lands; the gate covers a curated set.

### 8.5 Later metadata (P5a/P6a)

**P5a — slots 37/38:**

- `isatty(fd): bool`, runtime `i64 rt_isatty(i64 fd)`:
  `fd < 0` → false; `ioctl(fd, TCGETS 0x5401, &termios)` → true iff 0; pipe,
  file, `/dev/null`, closed fd → false. No Result, no allocation, no fd-table
  fatal. A tracked closed fd may have been reused by the kernel; the ioctl can
  observe the reused descriptor. Document and test pipe/file/`/dev/null`/pty
  and a closed tracked fd under `OBO_CHECK=1`; do not treat it as a fatal
  error.
- `getenv(name): ?[]byte`, runtime `u64 rt_getenv(u64 name)`:
  store `env_base = sbase + 8*(argc+2)` in `rt_init`; scan `envp` for
  `name=`; validate non-empty, no NUL, no `=`; first match wins; copy the
  value into an owned array; empty value is non-nil empty; absent is nil.
  No Result. `env::get/has/value/getOr` wraps it (section 5.8).
- `io::isTty(fd)` wrapper.
- Compiler: `resultCall` not needed; `rtCall1` for both. Add universe names,
  `callExempt` (getenv; isatty returns bool so `plausible` scalar is safe),
  builtin branches. Extend `tests/mutate_meta.py` with `isatty`/`getenv`
  runtime and compiler mutants and their cases; record the measured count.
  Bump `OBO_VERSION`; full gate; promote.

**P6a — slot 39:**

- `terminalWidth(fd): int`, runtime `i64 rt_term_width(i64 fd)`: `fd < 0` → 0;
  `ioctl(fd, TIOCGWINSZ 0x5413, &winsize)` with `{u16 row,col,xpixel,ypixel}`;
  return `ws_col` or 0; a zero column count is 0 (unknown). Caller falls back
  to 80.
- `io::termWidth(fd)` wrapper.
- Same stale-fd caveat as `isatty`.
- Extend `tests/mutate_meta.py` with `terminalWidth` mutants/cases; record the
  measured count.
- Only add if E2's real-TTY grid needs it; `--width`/pty tests can cover the
  layout without the builtin, but a real TTY default needs it for parity.
  Bump/promote as its own compiler feature.

**Dropped:** `currentDir`. No `--absolute` in E0/E1; a future phase adds it
with `getcwd` and a new slot if needed.

---

## 9. Differential harness, fixtures and goldens

### 9.1 Reference policy

- The reference is the pinned eza 0.23.3 release asset (section 3). It is
  invoked as `['eza', *args]` with `executable=<absolute path>`. With
  `[eza_binary, *args]` and `executable=...`, argv[0] is the absolute path,
  not `eza`; eza ignores argv[0], so this is only a wording/portability fix,
  but use the clean form. (`tools.py`'s GNU-coreutils rule is analogous.)
- `obo/tests/eza/VERSION` holds the exact `eza --version` output — the pinned
  release line is `v0.23.3 [+git]`, not `v0.23.3` — plus the tarball sha256,
  binary sha256 and URL. `REFERENCE.md` holds the probe report.
  `--require-reference` compares the exact three-line output, including
  `[+git]`.
- `--require-reference` requires the exact version output and binary hash;
  missing/different is a setup error (exit 2), never a skip. Hosts without eza
  may run committed goldens; the release review requires the live differential.
- A version bump is its own change with a fresh probe, re-recorded goldens and
  a re-read of every rule in section 3.3; do not track latest.
- Do not vendor the binary into the repository (size, licensing, supply
  chain). Download to a tools directory outside the repo and verify hashes.

### 9.2 Fixtures

Each case runs in a fresh temp directory generated by Python (no committed
binary trees), with explicit modes and mtimes and a fixed creation order.
`umask(0o022)` or explicit chmods; do not change mtimes after creation.

- empty directory; one file; one directory; mixed files/dirs/symlinks;
- hidden names: `.hidden`, `..double`, `.`, `..` (synthetic), `-`, `--x`;
- names with spaces, tabs, newlines, apostrophes, double quotes, backslashes,
  ESC, DEL, leading dash, length 1 and 255; all ASCII/valid UTF-8 for exact
  cases; one separate invalid-UTF-8 shape case (live/shape only until E2);
- comparator-tie fixtures `A`/`a`, `README`/`readme`, `pic4`/`pic 4`,
  `a`/` a` and a real ` .` beside synthetic `.`: these are **live tie probes**
  and never committed sort goldens (§7.5). Committed sort fixtures use names
  that are pairwise distinct under both `natcmp` and `natcmpFold`;
- permissions: 000, 600, 644, 755, 700, 4755, 2755, 1777; regular files,
  directories, FIFOs (`os.mkfifo`), sockets (Python bind); no device nodes,
  ACLs or xattrs;
- sizes: regular files 0, 1, 999, 1000, 1024, 1025, 4095, 4096, 1 MiB, sparse;
  **size-sort fixtures use regular files with distinct sizes**; directories
  only in live differential, because their sort key is host st_size even though
  long displays `-`;
- timestamps: 1970-01-01, 1999-12-31, 2000-02-29, 2020-01-02 03:04:05,
  2038-01-19, 2100-03-01, and one pre-1970 (long date `-`); `os.utime(...,
  ns=...)`, zero nanoseconds, distinct seconds for time-sort fixtures;
- symlinks: to a file, to a directory, to an executable, dangling, chained
  `a -> b -> a`, a target that is itself a symlink to a directory, target with
  spaces (`tar get`, `a b/target`; the broken `../tar get`, with no sibling
  (asserted absent) in the case directory's parent and no marker even with
  `-F=always`), target with a control byte, relative targets (no temp paths);
  absolute targets only in live/shape cases:
  `/tmp` (with and without `-F=always`), `/` (`-F=always` gives `//`), and a
  broken `/tar get` (displayed `'/tar get'` with no marker; it is not a valid
  root-parent case and its target is not created outside the fixture).
  A `-d sub/link` operand with executable and directory targets in `sub/`, and
  a spaced-parent operand `x y/sub/link` and directory `x y/sub/`. Default/
  `-1`/`-d` lines and long/tree must show ` -> target`; grid E2 must not.
- hard links: two names to one inode; E0 has no link column, so only for
  future `-H`;
- `-aa` dot synthesis on empty/mixed directories; a dotless operand `d`, a
  dotted one `dx.x` and an operand `.` (or `..`/`/`) for the extension key
  (`None`/`Some("x")`/`None`; `dx.` → `Some("")`); **`-aa`
  name-only is committed only for name/type/extension sorts with pairwise
  comparator-distinct names; `-aa --sort=size`/`--sort=modified` and `-aa -l`
  are live/shape-only** (section 9.4);
- tree: depth 1/2/3, empty dirs, hidden, symlink dirs (E1);
- grid: 1/2/10/100 short/long ASCII names at widths 20/40/80/120 (E2);
- operand cases for committed goldens use relative fixture-owned operands,
  including a spaced parent (`x y/sub/`, `x y/sub/f`, `x y/sub/link`);
  absolute operands (`/tmp/.../dir`) and `-d ..`/`-d /` are live/shape-only;
- long paths: a joined path near but under 4095 bytes; one over-limit case is
  an expected-error/limitation case, not a ref golden.

### 9.3 Case families and counts

Target about 200–240 cases (not thousands):

1. default/`-1`/`-a`/`-aa`/`-A`/`-A -a`/`-A -aa` on empty, one, mixed, hidden
   directories, including symlinks (targets shown in lines) (~30).
2. name sorting: default, `Name`, clusters, `-r`, `--group-directories-first`,
   `-D`, `-f`, both; natural/numeric/leading-zero/whitespace names (~35).
   Committed names are pairwise comparator-distinct; case-only/whitespace
   tie fixtures are live-only (§7.5).
3. size/modified/extension/type sorting with distinct keys and reverse (~35);
   extension sorting includes the `-aa` dot key for a dotless operand `d`
   (`None`), `dx.x` (`Some("x")`) and operand `.`/`..`/`/` (`None`).
4. restricted long on every type/size/mode/mtime, `-n` and `--no-user`,
   pre-1970, `-d` fixture-owned file/dir/symlink, symlink/dangling (~50);
   `-aa -l` is live/shape-only.
5. quoting: spaces/apostrophe/both/controls/backslash, symlink targets
   (valid/broken/error), `-F` interaction, the root-parent live cases `/tmp`
   and `/` with and without `-F=always`, the broken absolute `/tar get` live
   case (`'/tar get'`, no marker), and the broken relative `../tar get` case
   (`'../tar get'`, no sibling, no marker) (~30).
6. classify: `-F` auto, `-F=always`, spellings, markers per type, **lines/
   default/`-1`/`-d` target markers**, long target marker, broken target,
   grid marker-on-link deferred to E2 (~25).
7. `-d`/`--treat-dirs-as-files`/`--list-dirs` on relative fixture-owned
   file/dir/symlink, `-l -d`, trailing slash, operand display normalization
   (~15); absolute/`-d ..`/`-d /` are live/shape-only.
8. operands: relative file, `--`, dash names, hidden explicit operand (~10);
   absolute operands live/shape-only.
9. errors/exit: missing, permission (non-root), unknown/missing/forbidden/bad
   option, own restricted/unsupported errors, write error (~20).
10. memory: 10,000 entries long listing; a name-only listing; E1 tree case.

Each case compares exact stdout bytes, exact exit status, exact stderr for
error cases, and empty stderr for success. Under `OBO_CHECK=1`/`OBX_GC=poison`
the runner rejects any `obo:`/`obx:` defect line, and rejects exit 8 (leak) or
9 (double close/use-after-close) **in success cases**. Exit 3 is expected for
own/unsupported/option-error cases and is not a defect; reference exit 2 is
expected only for input-metadata-failure cases. The old blanket "no exit 3–9"
scan incorrectly flagged the tool's own usage errors. Output cap 16 MiB;
timeout 10 s (60 s for slow/memory cases); kill the process group on timeout.

### 9.4 Goldens and host stability (B15, v5)

- **Live differential is the primary gate.** Both tools run on the same
  fixture directory in the same process environment; stat data agree.
- `obo/tests/eza/golden/ref/<case>.{stdout,stderr,exit}` holds reference
  outputs; `own/<case>` holds our own usage/unsupported errors.
- **Committed long goldens** use
  `-l --no-user --bytes --time-style=long-iso`: no uid/gid column, so no uid
  width or root/non-root variation; regular-file sizes are pinned; directory,
  symlink, FIFO and socket sizes are `-`; link counts are absent; dates are
  UTC; modes/sizes/mtimes are pinned; no xattrs/device nodes; name-order
  goldens have pairwise comparator-distinct sorted names (the name tie rule
  below), while size- and date-order goldens need only distinct sort keys
  (distinct regular-file sizes or mtimes) and may have comparator-equal
  names. Size cells stay
  <1000 except the one recorded 7-digit `--bytes`/`LC_ALL=C` case; if P0 shows
  grouping, that case is the only grouped golden and the rest stay <1000.
- **Excluded from committed goldens** (live/shape-only):
  - `-aa -l` dot rows (`.` = listed directory mode/mtime; `..` = host temp
    parent);
  - **`-aa --sort=size` and `-aa --sort=modified`**: the output is names only,
    but the dot keys are the listed directory's and host parent's
    `st_size`/mtime (eza's synthetic dots have lazy metadata), so the order is
    host-dependent;
  - absolute operand display and absolute symlink targets, including the
    valid root-parent live cases (`/tmp`, `/tmp/` with `-F=always`, `/` with
    and without `-F=always`) and the broken absolute `/tar get` target
    (`'/tar get'`, no marker);
  - `-d .`, `-d ..`, `-d /`;
  - numeric-user (`-n`) long rows;
  - any case with host-dependent parent/cwd metadata not explicitly pinned.
  `-aa` **name-only** lines are committed for name/type/extension sorts only
  when every listed pair — including the synthetic dot names — is
  **comparator-distinct** under the comparator used (`natcmpFold` for
  default/name and lowercase extension; `natcmp` for `Name`,
  `Ext`/`Extension` and type). Comparator-equal names under the relevant
  comparator are live-only because eza's stable sort preserves `readdir`
  order; precisely, `A`/`a` and `README`/`readme` tie only under
  `natcmpFold` (live-only for fold orders, usable in `natcmp` orders when all
  other pairs are distinct), while `pic4`/`pic 4`, `a`/` a` and ` .`/`.`
  tie under both comparators (whitespace is skipped in both) and are live-only
  for every name-order golden. A name-only
  extension+`-aa` case uses a relative fixture-owned operand; its `.` key is
  `File::ext(dir.path)` — `None` when `Path(operand).file_name()` is `None`
  (operands `.`, `..`, `/`), otherwise `ezaExt` of the final name
  (`d` → None, `dx.x` → Some("x"), `dx.` → Some("")) — so the operand's final
  normal component matters and must be part of the case definition.
  `-aa -l` and the size/modified name-only sorts may be committed only if the
  fixture directory **and its parent** are explicitly `chmod`/`utime`-pinned,
  their mode/mtime/`st_size` values are recorded in the manifest and compared
  before use (a mismatch skips the case), and the parent's metadata is
  recorded; otherwise they are live. Directory `st_size` is filesystem-
  dependent, so a size-sorted pinned case is meaningful only on a matching
  recorded filesystem.
- **Numeric-user long** (`-n`) is live-differential only, or recorded with a
  per-host manifest. A manifest records euid, uid digit width, umask,
  filesystem type, locale, reference hash, and cwd/parent mode+mtime if any.
  Goldens are compared only when the manifest matches; otherwise the case is
  skipped with a note or re-recorded. Do not substitute `<uid>` without
  repadding: the column width changes the whole line.
- If placeholders are used, record the cell width and re-render the actual
  uid into the same width before substitution; prefer `--no-user` and live
  differential to this complexity.
- **`--record` refuses or normalizes absolute output.** It requires the
  reference, runs the same fixture/case definitions, refuses to overwrite a
  differing golden without `--force`, and refuses any **stdout or stderr**
  line containing the case directory or a leading `/` path (path leakage).
  Committed cases therefore use relative operands/targets; absolute-output
  cases are live/shape-only. The recorded manifest is verified by the runner.
- Exclude or normalize: numeric uid widths, any future link count, directory
  sizes/nlink, inode values, device major/minor, xattr `@`, root/non-root
  permission differences.
- Size-sort goldens use regular files with distinct sizes; date-sort goldens
  use distinct whole seconds. Comparator ties are tested live only for every
  sort — size/date, name/type/extension, and `-aa` dot names — because their
  order is readdir order. Committed cases require comparator-distinct keys or
  names.
- Inode numbers are host-dependent; `-i` is out. `--sort=none` is out.
- Root: permission-denied and unreadable fixtures are skipped when
  `os.geteuid()==0`; do not run a mixed-user matrix.
- Filesystem: local temp filesystem; do not assume directory-entry order;
  no `--sort=none`. Use relative fixture-owned operands for any committed
  operand case; test normalization (`a//b`, `a/./b`, `dir/`) separately.

### 9.5 Environment pin

For reference and our tool in every case, start from a minimal environment:

```
PATH=/usr/bin:/bin
LC_ALL=C
LANG=C
LANGUAGE=C
TZ=UTC
HOME=<case home>
TMPDIR=<case tmp>
```

Unset (unless the case sets it): `EZA_COLORS`, `LS_COLORS`, `EXA_COLORS`,
`NO_COLOR`, `COLUMNS`, `EZA_GRID_ROWS`, `EXA_GRID_ROWS`, `EZA_ICONS_AUTO`,
`TIME_STYLE`, `TERM`, `EZA_STRICT`, `EXA_STRICT`, `EZA_DEBUG`, `EXA_DEBUG`,
`EZA_CONFIG_DIR`, `XDG_CONFIG_HOME`, `EZA_ICON_SPACING`, `EXA_ICON_SPACING`,
`EZA_OVERRIDE_GIT`, `EXA_OVERRIDE_GIT`, `EZA_MIN_LUMINANCE`,
`EXA_MIN_LUMINANCE`. `EZA_STRICT`/`EXA_STRICT` would turn duplicate flags into
errors; `EZA_DEBUG`/`EXA_DEBUG` add logging; `EZA_CONFIG_DIR`/`XDG_CONFIG_HOME`
change the theme; the luminance variables change colour scaling; the icon/git
variables affect out-of-scope features. None affects E0 output, but all are
pinned so E1 colour/theme probes (and a future colour gate) cannot pick up a
user setting. For colour/grid cases set exactly the tested values. `LC_ALL=C` makes month names, sort order,
error text and the numeric thousands separator stable. A non-C locale is a
separate probe, not a gate.

### 9.6 `tests/eza.py`

Model it on `tools.py` but with no Python model of eza (the reference/goldens
are the oracle; a model would duplicate the tool and not be independent).

- Options: `-k`, `-v`, `-x`, `-j`, `--fast`, `--list`, `--record`,
  `--force`, `--require-reference`, `--reference=auto|eza|none`, `--memory`.
- Builds eza through the **single** entry point
  `build_tools.build(['eza'], private_out, obo, rt)` (there is no
  `build_modules`); the output dir has its own `sym/`.
- `--record` writes ref and own goldens and `VERSION`/manifest; `--list`
  prints case tags.
- Tags: `ref` (live differential required), `golden` (recorded reference,
  usable without eza), `own` (our usage/unsupported error), `skip`
  (platform/root). A golden case is also a `ref` case when eza is present;
  compare both.
- Defect scan: reject `obo:`/`obx:` lines in any case; reject exit 8/9 (leak/
  double close) in success cases. Exit 3 is expected for own/unsupported/
  option-error cases (not a defect); reference exit 2 only for input-metadata
  failures. Do not scan for a blanket "exit 3–9".
- Exit codes: 0 pass, 1 test failure, 2 harness/setup failure.
- Factor generic helpers from `tools.py` (`run_proc`, golden load/write,
  `normalize_paths`, environment/probe classification) into `tests/diffutil.py`
  only if `tools.py` semantics are unchanged; run `tools.py` before/after the
  factor. If risky, copy the ~150 lines into `eza.py`; duplication is cheaper
  than a regression in a green gate.

### 9.7 Memory and leak cases

- Build eza; create 10,000 entries; run
  `eza -l --no-user --bytes --time-style=long-iso` and a name-only listing
  under `OBO_STATS=1`, `OBO_CHECK=1`, `OBX_GC=poison`; parse the single
  `obo: N peak bytes, M allocated` line; assert a recorded `EZA_PEAK_MAX` and
  no leak line. Set the cap from the first measured run plus a margin; do not
  guess.
- Also run a tree case (E1) to cover iterator buffers.
- Success cases must return from `main`; error-path `exit` skips stats/leaks
  and is not a memory case.
- The eza memory case is independent of the compiler `PEAK_BUDGET`; record the
  binary size and peak in README/FORK after measurement.

### 9.8 Import and stdlib tests

- `obo/tests/imports.py` (section 4.2) compiles every tool, every stdlib
  module, and the collision/decoy trees; runs in P1+.
- `obo/tests/stdlib.py` covers every `pub` API of P1 modules:
  - `strings`: `split`/`splitByte`, `join`, `trim`/`trimLeft`/`trimRight`,
    `cmp`/`eq`, `startsWith`/`endsWith`, `contains`/`indexOf`/`byteAt`,
    `fromBytes`, `lowerAscii`, `natcmp`/`natcmpFold` tables, `quoteName` and
    `parentPrefix` tables, and the classifiers `isSpace`/`isBlank`/`isPrint`/
    `isWordByte`/`isDigit` plus `displayWidth`;
  - `paths`: `isAbsolute`, `join`, `ext`, `hasTrailingSlash`, `isDotName`,
    `lastComponent`/`parentOf` table, operand-display normalization, and the
    explicit absence tests for removed `base`/`dir`/`stem`/`clean`;
  - `sort`: empty/one/duplicates, stability, `sortOrder*`, `reverse`,
    natcmp/natcmpFold orders, extension/type two-pass pipelines;
  - `cli`: every spelling in section 5.6, bounds, optional values, repeats,
    `--`, clusters, errors;
  - `time`: civil dates, leap years, negative floor division, long-iso.
- P2 adds `errors` tests (text/osError/debugPath/exit constants) and io/fmt
  tests through `io_test.py`; P3 adds `fs` tests through `tests/meta/*.obx`
  and `rt_meta_test.c`; P4 adds the eza CLI target-resolution cases
  (`-d sub/link`, target-is-symlink, chain/loop) and the operand-`.`/`..`/`/`
  extension key cases (`File::ext(dir.path)` → `None`) in `tests/eza.py`;
  P5a adds env/isatty/getenv tests.
- Use a headerless test root per case with `-I lib` when running from the
  `obo/` cwd (the Makefile's cwd), or `-I obo/lib` from the repository root;
  use one cwd consistently. The merged harness helpers are cwd-independent:
  `tests/io_test.py` and `port_compare.py` compute `LIBDIR = <obo>/lib`, and
  `run_fork.py` passes `-I <FORK>/lib`; all are absolute paths. Pass a
  private sym-dir; no concatenation.

---

## 10. Build, migration and tooling

### 10.1 `build_tools.py`, Makefile and reference install

**Merged builder state (verified at `84558da`; unchanged by the size fixes).**
`obo/tools/build_tools.py` already compiles each module root directly, has no
concatenation/generated header/line map, creates a private `<out>/sym` in
`_symdir(out)`, passes `--sym-dir` to `probe()` and every `_compile`, and
exposes one entry point `build(names, out, obo, rt)`. `_map_error(stderr)`
shortens absolute paths under `ROOT` to repo-relative names in compile
diagnostics; it is **not** a generated-line mapper and **stays**. There is no
global `SYMDIR`.

P2's builder changes are therefore:

- `SRCDIR = 'cmd'` (from `programs`); `TOOLS = ('cat', 'wc', 'sort')` stays.
- P4 extends the allowed names with `'eza'` (`EZA = ('eza',)`,
  `ALL = TOOLS + EZA`). **One entry point:**
  `build(names, out, obo, rt)`. There is no `build_modules`; `tests/eza.py`
  calls `build(['eza'], ...)`. `build` validates every name against the names
  that exist in that stage.
- `LIB_MODULES` grows to include `fmt.obx`/`errors.obx` when they appear, so a
  missing module is a `BuildError`, not a compiler surprise.
- Keep `_compile`, `_read`, `_symdir`, `_map_error`, `probe`, `BuildError`.
  Nothing is deleted to match v3: the concatenation helpers do not exist on
  merged main.
- Compile command per tool:
  ```
  <obo> <ROOT>/cmd/<name>.obx -o <out>/<name> --rt <rt> --sym-dir <out>/sym
  ```
  No `-I`, no concatenation, no generated header, no line map. Compiler errors
  point at `cmd/*.obx` or `lib/*.obx` directly. Explicit `../lib/*.obx` imports
  in the tool sources make `-I` unnecessary; tests that import plain `"io.obx"`
  from `tests/lib`/`tests/run` pass `-I <libdir>`.
- The builder creates `<out>/sym` before compiling; it must exist. `<out>` is
  the requested private/output directory. Default `make tools` uses
  `obo/build/tools`; `make eza` uses `obo/build/eza`.
- Stage 2 emission applies: headerless `cat`/`wc`/`sort` roots emit no root
  `.sym`; dependencies' syms are written; headed `eza` emits `eza.sym`.
- Keep the phase-2 builtin probe (openFile/readFd/writeFd/closeFile). After
  P3 add a metadata probe that **runs and exercises one slot**: build a tiny
  program that calls `statPath` on a temp file the builder creates and checks
  the returned mode/size (and, under `OBO_CHECK=1`, leaks), then exits. A
  compile-only probe built by the new compiler detects an **old compiler**
  (unknown builtin), not an old `runtime_text.bin`: the slot name is resolved
  at compile time. Either run one slot (preferred) or have the builder compare
  a runtime identity/version; do not describe a compile-only probe as catching
  an old runtime.
- `TOOLS`/`EZA` are separate so `make tools` does not require the metadata
  runtime before eza exists. `tests/tools.py` calls `build_tools.build` with
  `TOOLS`; `tests/eza.py` calls it with `['eza']`.

Makefile:

```make
.PHONY: tools tools-check tools-memory eza eza-check eza-memory all clean
OUT_TOOLS = build/tools
OUT_EZA   = build/eza

tools: obo runtime_text.bin runtime_data.bin | $(OUT_TOOLS)/sym
	python3 tools/build_tools.py --out $(OUT_TOOLS)

eza: obo runtime_text.bin runtime_data.bin | $(OUT_EZA)/sym
	python3 tools/build_tools.py eza --out $(OUT_EZA)

$(OUT_TOOLS)/sym:
	mkdir -p $@
$(OUT_EZA)/sym:
	mkdir -p $@

tools-check: tools
	python3 tests/tools.py
eza-check: eza
	python3 tests/eza.py --require-reference
tools-memory: tools
	python3 tests/tools.py --memory
eza-memory: eza
	python3 tests/eza.py --memory
```

- Sym dirs are **order-only** (`|`); a sym timestamp must not rebuild every
  tool. The builder also creates them, so the Make rule is a safety net.
- `make -j tools eza` writes distinct out/sym dirs; no shared `.obo/sym`.
- There is no `SYMDIR` variable on merged main; nothing to remove. `make clean`
  removes `build`, which contains `build/tools/sym`. The generic `.obo/sym`
  convention remains only for other Stage 2 builds/tests, never as a tool
  build input.
- `make promote` is unchanged except for the standard compiler gate.

Reference install in CI (example; hash-pinned):

```sh
EZA_DIR="$RUNNER_TEMP/eza-ref"; mkdir -p "$EZA_DIR"
curl -fsSL -o "$EZA_DIR/eza.tar.gz" \
  https://github.com/eza-community/eza/releases/download/v0.23.3/eza_x86_64-unknown-linux-gnu.tar.gz
echo "02e818f7b06f2c637b25761731104f1e9d6ea8f6e5924ab05eae69db4ed072a5  $EZA_DIR/eza.tar.gz" \
  | sha256sum -c -
tar -xzf "$EZA_DIR/eza.tar.gz" -C "$EZA_DIR"
echo "10457dde35808517d09bcd3bfb6bc386000d06c2126e34b9071e09470a8f7cd8  $EZA_DIR/eza" \
  | sha256sum -c -
PATH="$EZA_DIR:$PATH" python3 obo/tests/eza.py --require-reference
```

The tarball is downloaded data; extract it outside the repository and never
commit it.

### 10.2 Sym-dir rules (B13)

Merged main already implements the rules below for the tools, `io_test.py`,
`run_fork.py`, `port_compare.py` and `mutate_io.py`; P2/P4 keep them when
sources move and add `eza`.

- One sym-dir per output/build: `build/tools/sym`, `build/eza/sym`, and a fresh
  `tempfile.mkdtemp(prefix='sym-', dir=<test tmp>)` for every test compile.
- `build_tools.build` creates `<out>/sym`; callers pass distinct `out`.
- Test helpers pass the module flags through `run.compile_with`; merged main
  has **no global `module_args(lib, symdir)` helper**. The real forms:
  ```python
  # tests/io_test.py case_lib
  run.compile_with(run.OBXC, text, 'lib_' + name,
                   extra_args=['-I', LIBDIR, '--sym-dir', symdir])
  # tests/run_fork.py program_test
  run.program_test(p, extra_args=['-I', LIB, '--sym-dir', symdir])
  # tests/port_compare.py has a local module_args(symdir, needs_modules),
  # with a different signature from the illustrative (lib, symdir) form
  ```
  Each compile gets its own `symdir`; never reuse one across parallel tests.
- `run.compile_with(compiler, src, name, extra_args=())` passes the args
  through; `run.program_test(path, gc_mode='poison', extra_args=())` does too.
- `port_compare.outcome(..., extra_args=())` creates a private sym-dir per
  compiler per case; module tree cases get their own too.
- `io_test.py case_lib`, `case_io`, `mutate_io.py run_case` create per-case
  sym-dirs.
- No test writes a `.sym` next to a source; the default sym-dir is never used.
- Stage 2 concurrency rule: writes truncate in place, so sharing a sym-dir
  across concurrent builds is forbidden.

### 10.3 P2 migration checklist (B14)

**Already done on merged main (do not redo):** `lib/io.obx`/`lib/strings.obx`
modules; module-root tools in `programs/`; the direct-compile builder with
private `<out>/sym`; qualified `tests/lib`/`tests/run/io_*` roots;
`run.compile_with`'s `extra_args`; module args in `run_fork`/`port_compare`;
the mutated-module-tree `mutate_io`; `case_lib` with `-I lib`. The v3 steps
that delete/rename `.obo` files, "remove the prelude", or "add the module tree"
are deleted from the checklist rather than run as no-ops.

Files (the P2 change):

- Edit `lib/strings.obx`: remove `cmpBytes`, `errText`, `digits`,
  `appendPadded`; `strings::cmp` and the rest of §5.1 remain.
- Edit `lib/io.obx`: remove `writePadded`; add `writeByte`; keep the merged
  `pub` surface and `clampChunk`.
- Add `lib/fmt.obx` (digits/appendPadded/writePadded/modeString/…).
- Add `lib/errors.obx` (`text` plus the §5.7 helpers); the tools switch from
  `strings::errText` to `errors::text` in this change.
- Move `programs/{cat,wc,sort}.obx` → `cmd/{cat,wc,sort}.obx`; add the imports
  and keep each tool's `setOpt`, argv loop, numeric parser, width rule,
  numbering state and messages. Keep the builtin `args()`; no `cli` adoption.
- `cmd/cat.obx`: `strings::appendPadded` → `fmt::appendPadded`;
  `strings::errText` → `errors::text`; import `fmt`/`errors`; it may keep
  direct `w.buf` appends or switch to `io::writeByte`.
- `cmd/wc.obx`: `io::writePadded` → `fmt::writePadded`;
  `strings::digits` → `fmt::digits`; `strings::errText` → `errors::text`;
  output byte-identical.
- `cmd/sort.obx`: `strings::cmpBytes` → `strings::cmp`; `strings::errText` →
  `errors::text`; imports no `sort`.
- `cmd/eza.obx` is P4, not P2.
- `tests/lib/write_padded.obx`: `io::writePadded` → `fmt::writePadded`; add
  `import "fmt.obx" as fmt` **and keep** `import "io.obx" as io` (it still
  declares/uses `io::Writer`, `io::newWriter`, `io::write` and `io::flush`).
  The other three roots do not change (exact names in §1.1a item 5).
- `tests/run/io_reader.obx`/`io_writer.obx`: no change (already module roots
  importing `"io.obx"`); `run_fork.py`, `obx/tests/run.py` and
  `port_compare.py`: no change beyond the `port_compare.py` comment in §10.4.
- `tests/mutate_io.py`: the scratch module tree **already** copies every
  `lib/*.obx` except `io.obx`, so `fmt.obx`/`errors.obx` join it automatically.
  Re-anchor the seven `LIB_MUTANTS` patterns against the edited `lib/io.obx`
  and keep the exactly-once assertion. The count stays 27/27 unless a pattern
  legitimately moves; if it does, record the new count. `mutate_port.py` has
  no prelude comment; no change. P3's new `readDirFd` runtime code must stay
  textually distinct from these runtime anchors (`a->len = r`,
  `tracked && fdstate[fd] == 2`), or the anchors are re-anchored in the P3
  commit and the count re-recorded (§6.8).
  **Coverage boundary:** `mutate_io` stays 27/27 (20 runtime + 7 library) and
  gains **no** mutant for the new `io::writeByte`, `fmt` or `errors` code.
  Those functions are covered by byte-equality tests in `io_test.py`/
  `stdlib.py`, not by mutation. If the user requires mutation coverage, add
  named `writeByte`/`fmt`/`errors` mutants as a separate measured extension
  and record the new count; do not change `27/27` without measuring, and do
  not describe 27/27 as covering the new code.
- `tests/tools.py`/`tests/io_test.py`: case definitions, models, goldens and
  the streaming test unchanged; only `build_tools.SRCDIR` and the private
  out/sym layout are involved.
- `build_tools.py`: §10.1 (`SRCDIR='cmd'`, single `build`); no `-I`.
- Docs: `programs/README.md` → `cmd/README.md` (tools, build commands, no
  concatenation, eza section in P4), `README.md` tree/build/status (the merged
  README still says the tools stay in `programs/` and `io::writePadded`
  stays), `LANGUAGE.md` I/O library path and builtins, `FORK.md` design record.
  Also update the stale `programs/` paths/comments in `tests/io_test.py` (the
  module docstring and its `tools` section), `tools/build_tools.py` (docstring
  "Each tool source under programs/" and the `error`/diagnostic text naming
  `programs/<tool>.obx`), and the `Makefile` comment above the `tools` target
  that says `each programs/*.obx is a module root`. P2 must leave no stale
  `programs/` path behind.

Gates for P2 (current size-fixed-baseline figures from §2.1, plus the
P1-created stdlib/import tests):

```
make tools
python3 tests/tools.py --require-reference       # exactly 199/199
python3 tests/tools.py --memory                  # 2/2
python3 tests/io_test.py --rt --programs --tools
python3 tests/imports.py                         # stdlib + tool matrix (P2)
python3 tests/stdlib.py
python3 tests/run_fork.py                        # 1,130/1,130
python3 tests/elf_test.py                        # image/runtime_data_segment + synthetic images
python3 tests/port_compare.py                    # 1,147/1,147; peak 1,636,176 < 2,000,000
OBO_REF=seed/obo python3 tests/port_compare.py   # 1,025/1,025
python3 tests/mutate_fork.py                     # 527/549 + 22 equivalent
                                                 # (SIZE_MUTANTS: 4 caught + 1 equivalent)
python3 tests/mutate_port.py                     # 495/495
python3 tests/mutate_io.py                       # 27/27 runtime/library, mutated tree
                                                 # (no writeByte/fmt/errors mutants; §10.3)
python3 tests/port_test.py                       # 486/486
python3 tests/sym_test.py                        # 27/27 (no version bump in P2)
python3 tests/sym_fuzz.py                        # 200/200
python3 tests/doc_check.py                       # all examples pass (45 at the current baseline)
python3 tests/homes_check.py
python3 tests/homes_edge.py                      # 1,028 statements; 0 differ
python3 bench/bench.py
```

Tool stdout/stderr/exit and goldens must be byte-identical; memory cases must
pass the existing `MEMORY_PEAK_MAX` (record actual peaks; do not raise the cap
to accommodate a regression). No compiler change means no `make promote`, but
all compiler gates still run to prove the module builds did not perturb the
toolchain. The counts in the block above are current baselines: P2 records
the measured `mutate_fork`/`mutate_port`/`mutate_io`/`doc_check` counts after
its cold run. If the lib move changes a mutant outcome or makes a pattern
STALE, re-anchor that mutant in the same commit and record the new count; do
not skip it. If P2 adds doc examples, `doc_check` records the new total
(45 is the current baseline, not a hardcoded gate).

### 10.4 Documentation corrections

- `port_compare.py`: replace the stale 954,896 comment with the current
  1,636,176 measured peak, 35,076,608 total allocation and the unchanged
  2,000,000 budget rationale; note the figure is for the current tree and varies
  by source path.
- `README.md`: current status date and table; tool paths `cmd/`; eza phase
  status; measured memory, tool/eza binary sizes and peaks. Confirm the current
  size-fix figures (10,448 lines / 382,329 bytes source; 421,889-byte seed and
  compiler, sha256 `55dab101…`; 1,636,176 live / 35,076,608 allocated / 363,824
  spare; 1,130 tests (including `image/runtime_data_segment`); 527/549 + 22
  mutants; 495 port mutants; 27/27 mutate_io; 27 sym tests; 200 fuzz; 45 doc
  examples; 199/199 tools + 2/2 memory; `elf_test`; tool
  binaries 36,865 / 40,961 / 45,057; minimal 28,673) rather than restoring the
  pre-size-fix figures. Update the module state (the merged README's "tools
  stay in `programs/`, `io::writePadded` stays" sentences are true for main but
  must be updated by P2). No unmeasured "tens of KB" claims.
- `FORK.md`: design record for this phase; limitations (path limit/race,
  non-UTF-8, UTC, restricted long, reference pin, TTY divergence, host-golden
  exclusions); permission/broken-pipe probe notes; `OBO_VERSION` bump/rebuild
  notes; per-feature slot table; size-fixed current baseline and the
  `tests/elf_test.py`/`SIZE_MUTANTS` record.
- `LANGUAGE.md`: builtin table (`readDirFd`, `statPath`, `lstatPath`,
  `readLinkPath`, later ones marked later); `fs` skip-dots contract; stdlib
  module paths; owned-temporary `env::has` example; no concatenation.
- `cmd/README.md` (moved): cat/wc/sort docs unchanged in semantics; eza E0
  flags, restricted long, quoting, exits, divergences.
- `tests/eza/VERSION`, `REFERENCE.md`: data, committed.
- `README.md`/`FORK.md` "section 10" stale LANGUAGE references: fix while
  editing docs (review finding 9 of the module review; non-blocking).

### 10.5 Versioning, rebuilds and measurement

- P3 bumps `OBO_VERSION` from `0.2` to `0.3`; P5a bumps it to `0.4`; P6a, if
  used, bumps it to `0.5`. Each rebuilds the seed via `make promote` after the
  full gate. `obosym`/`abi` do not change unless the modules design says
  otherwise.
- After a bump, rebuild tools and tests; do not run `--check` against sym dirs
  produced by the previous compiler. Document the rebuild step.
- **Update `tests/sym_test.py` in the same commit as the bump:** the module's
  `OBO_VERSION = '0.2'` constant and its literal `compiler "0.2"` expectations
  in `stale_missing_corrupt_versions()` (the compiler-mismatch case),
  `handwritten_interface()` and `b6_stub_completion()` must all move to the new
  version. Leaving them stale fails `--version` and the mismatch case. Then run
  `python3 tests/sym_test.py` (27/27) and `python3 tests/sym_fuzz.py`
  (200/200) as part of the full compiler gate. P3 (`0.2`→`0.3`), P5a
  (`0.3`→`0.4`) and P6a (`0.4`→`0.5`) each own this edit.
- Measure after each compiler feature: `runtime.elf` and `runtime_text.bin`
  sizes, each tool/eza binary size, the compiler self-compile live peak and
  total allocation, and eza's 10,000-entry peak. The size-fix baseline is
  1,636,176 live / 35,076,608 allocated on a 382,329-byte source, 0.11 s; use
  it as the comparison point. Record in README/FORK. No gate
  on exact size; no `PEAK_BUDGET` raise. If the peak approaches 2,000,000, use
  the cut order in section 6.8 and stop for the user.

---

## 11. Staged order, parallelism and gates

### 11.1 Stages

**P0 — reference, probe, fixtures, harness (can run now, parallel with P1).**

- Acquire/pin eza 0.23.3, verify hashes, run the probe; commit
  `tests/eza/VERSION`, `REFERENCE.md`; write the fixture generator and
  `tests/eza.py` skeleton; record the initial golden matrix. The build step can
  be stubbed until eza exists.
- Adds new Python/test/golden files only; does not touch compiler, runtime,
  Makefile or existing tools. No obo build required.

**P1 — stdlib modules (can run now on current size-fixed main; Stage 2 surface;
edits `strings.obx` in place).**

- **Edit `lib/strings.obx` in place**: add the §5.1 API and keep
  `cmpBytes`/`errText`/`digits`/`appendPadded` as temporary compatibility
  functions (the merged tools call them). Add `paths.obx`, `sort.obx`,
  `time.obx`, `cli.obx` with the §5 APIs. Do **not** add `env.obx`, `fs.obx`,
  `fmt.obx`, `errors.obx` or replace `io.obx`: they belong to P5a, P3 and P2
  (and `env`/`fs` need builtins that do not exist yet).
- Add `tests/stdlib.py`, `tests/stdlib/`, `tests/imports.py`.
- Gate: `python3 tests/stdlib.py` and `python3 tests/imports.py` (stdlib
  matrix) with the current size-fixed compiler (Stage 2 surface) and private
  sym-dirs, **plus
  `make tools`, `python3 tests/tools.py --require-reference` (199/199),
  `python3 tests/tools.py --memory` (2/2) and
  `python3 tests/io_test.py --tools`** to prove the in-place `strings.obx`
  edit is build-neutral. No compiler/runtime/Makefile/tool-source/docs changes. The P1
  compile matrix has no tool sources (§4.2); the collision/decoy diagnostics are
  the Stage 2 ones.

**P2 — io/fmt and tool migration (after P1; P3 may be based on P2).**

- Remove the four `strings` compatibility functions and `io::writePadded`; add
  `lib/fmt.obx`, `lib/errors.obx`, `io::writeByte`; move tools to `cmd/`;
  change the builder's `SRCDIR`; update tests/docs per §10.3. No `.obo`
  delete/rename exists to do.
- Gates in §10.3; no compiler change; no promote.

**P3 — runtime metadata 33–36 (after P2, on the size-fixed seed `84558da`, 421,889 bytes).**

- Implement `sys4`, EINTR retry/fault hook, four slots, compiler builtins,
  `fs.obx`, `tests/rt_meta_test.c`, fs-level `tests/meta/*.obx`, compiler
  cases, and `tests/mutate_meta.py` with its two runners (mutated
  runtime/`entry.S` and mutated compiler; counts measured separately), docs;
  update `tests/sym_test.py`'s version constants and bump `OBO_VERSION` from
  `0.2` to `0.3`.
- Full compiler gate (section 11.2; baseline run_fork 1,130, `elf_test`,
  `sym_test` 27/27, `sym_fuzz` 200/200, port comparison 1,147/1,147 and
  1,025/1,025, mutate_fork 527/549 + 22, mutate_port 495/495, port_test 486,
  doc_check 45, plus P3's measured additions); independent review; merge;
  `make promote` once; seed promoted.

**P4 — eza E0 (after P3 seed).**

- Implement `cmd/eza.obx` with E0 scope, including the `-d sub/link`,
  target-is-symlink and chain/loop target-resolution cases in `tests/eza.py`;
  build via `build_tools`; use the P0 fixtures/goldens.
- Gate: `tests/eza.py --require-reference` E0; `tests/eza.py --memory`;
  all existing tools/stdlib/compiler gates (compiler unchanged); record
  `EZA_PEAK_MAX`. Review; merge; no seed promote.

**P5a — metadata 37/38 (compiler feature).**

- `isatty`, `getenv`, `rt_init` `env_base`, compiler branches, `env.obx`,
  `io::isTty`, tests, `tests/mutate_meta.py` extensions in both runners with
  measured counts, `tests/sym_test.py` version constants, `OBO_VERSION` bump.
- Full compiler gate (section 11.2; baseline run_fork 1,130, `elf_test`,
  `sym_test` 27/27, `sym_fuzz` 200/200, mutate_fork 527/549 + 22, mutate_port
  495/495, port_test 486, doc_check 45, plus P5a's measured additions); review;
  merge; promote.

**P5b — eza E1 (after P5a seed).**

- Colour (`--color`, `NO_COLOR`, default palette, `LS_COLORS`/`EZA_COLORS`
  subset) and tree (`-T`/`-L`); `env` usage; long colour only if probed.
- Gate: E1 differential, tools/stdlib/compiler unchanged, memory (including a
  tree case). Review; merge.

**P6a — metadata 39 (`terminalWidth`) if needed.**

- Implement slot 39 + `io::termWidth`; extend `tests/mutate_meta.py` with
  `terminalWidth` mutants/cases in both runners (measured counts); update
  `tests/sym_test.py`'s version constants; full compiler gate (section 11.2;
  baseline run_fork 1,130, `elf_test`, `sym_test` 27/27, `sym_fuzz` 200/200,
  mutate_fork 527/549 + 22, mutate_port 495/495, port_test 486, doc_check 45,
  plus P6a's measured additions); promote.

**P6b — eza E2.**

- Grid (`--width`/pty, layout, `-x`), UTF-8 width, non-UTF-8 lossy display.
- Gate: grid/UTF-8 differential and memory; no compiler change.

### 11.2 Full compiler gate (P3/P5a/P6a)

```
python3 tests/run_fork.py                       # 1,130 baseline + the new cases;
                                                # all pass
python3 tests/elf_test.py                       # image/runtime_data_segment
python3 tests/port_compare.py                   # 1,147/1,147 baseline; live peak
                                                # must be < 2,000,000; record the
                                                # measured peak (current 1,636,176)
OBO_REF=seed/obo python3 tests/port_compare.py   # 1,025/1,025
python3 tests/mutate_fork.py                    # cold; 527/549 + 22 equivalent
                                                 # (five SIZE_MUTANTS: four
                                                 # caught, one equivalent),
                                                 # plus new mutants killed/proved
python3 tests/mutate_port.py                    # 495/495 plus new
python3 tests/mutate_io.py                      # 27/27 I/O runtime/library;
                                                # record the measured count; new
                                                # readDirFd lines stay distinct (§6.8)
python3 tests/mutate_meta.py                    # all metadata mutants killed;
                                                 # runtime/entry.S and compiler
                                                 # runners reported N/N separately
                                                 # (counts measured and recorded)
python3 tests/port_test.py                      # 486/486
python3 tests/sym_test.py                       # 27/27 (version constants
                                                 # updated in the bump commit)
python3 tests/sym_fuzz.py                       # 200/200
python3 tests/doc_check.py                      # all examples pass
                                                 # (45 at the current baseline)
python3 tests/homes_check.py
python3 tests/homes_edge.py
python3 bench/bench.py
python3 tests/io_test.py --rt --programs --tools
python3 tests/meta_test.py                      # new in P3; env tests in P5a
python3 tests/stdlib.py
python3 tests/imports.py
make tools && python3 tests/tools.py --require-reference   # 199/199
python3 tests/tools.py --memory                 # 2/2
make promote                                    # seed == obo == stage2 == stage3
```

Then an independent review; merge; promote the seed once. Do not run a
compiler feature concurrently with another compiler feature.

### 11.3 Eza gates (P4/P5b/P6b)

```
make eza
python3 tests/eza.py --require-reference        # exact version/hash required
python3 tests/eza.py --memory
python3 tests/imports.py
python3 tests/stdlib.py
python3 tests/io_test.py --rt --programs --tools
python3 tests/elf_test.py                       # compiler/image gate unchanged
python3 tests/tools.py --require-reference      # tools unchanged
python3 tests/tools.py --memory
```

E0's `--require-reference` is the differential stop condition. A host without
the reference may run committed goldens, but the release review must run the
live differential and record the exact version/hash.

### 11.4 Gates that must never be weakened

- `tests/tools.py` exact output/goldens and `--memory` after migration.
- `tests/io_test.py` runtime/program/tool streaming and fd oracles.
- `tests/stdlib.py`, `tests/imports.py`, `tests/meta_test.py`/`rt_meta_test`,
  and `tests/mutate_meta.py` (all metadata mutants killed; measured count).
- `tests/eza.py` differential/goldens/memory; missing reference is a setup
  failure for `--require-reference`.
- `tests/sym_test.py` (27/27) and `tests/sym_fuzz.py` (200/200) after every
  compiler feature and every `OBO_VERSION` bump; the bump commit updates
  `tests/sym_test.py`'s module-level `OBO_VERSION` and its `compiler "0.2"`
  literals in `stale_missing_corrupt_versions()`, `handwritten_interface()` and
  `b6_stub_completion()` rather than weakening the test.
- `tests/elf_test.py` and the `image/runtime_data_segment` case inside
  `tests/run_fork.py` after any compiler change that touches image emission or
  the epilogue; the generated bytes are allowed to change, the segment shape
  and behaviour are not.
- Compiler suite (`run_fork` 1,130 baseline plus the feature's new cases, all
  pass; `sym_test.py` 27/27; `sym_fuzz.py` 200/200; `elf_test.py`), port
  comparison (1,147/1,147 baseline; 1,025/1,025 against the seed; each
  feature records its **measured** live peak, which must stay < 2,000,000;
  current baseline 1,636,176), both mutation
  suites (527/549 + 22, the five `SIZE_MUTANTS` being four caught and one
  equivalent; 495/495),
  `mutate_io` (27/27 measured, mutated module tree; no writeByte/fmt/errors
  mutants — stated boundary; new `readDirFd` runtime lines must not duplicate
  an existing anchor or the anchor is re-anchored and the count re-recorded),
  `mutate_meta` (all killed;
  runtime/`entry.S` and compiler-runner counts measured separately; the
  compiler runner's fd-materialisation case is the `mut &int` `max`
  expression), homes
  checks, docs (all examples pass; 45 at the current baseline), memory budget
  and fixed point after each compiler feature; no `PEAK_BUDGET` raise.
- No test weakening or golden update to accommodate a divergence: restrict the
  eza subset and document it, or reject the flag. The 199/199 and 2/2 tool
  results are byte-identical invariants.

---

## 12. Risks, evidence and user decisions

### 12.1 Technical risks

1. **Runtime slot/ABI mistakes.** Wrong `entry.S` order, missing `r10` in
   `sys4`, wrong descriptor or field offset. Mitigations: append-only slots,
   both sequences in one commit, a C harness for every slot/offset, mutants
   swapping slots/fields (mutated-runtime runner), builtin-branch mutants
   (mutated-compiler runner), `tests/elf_test.py` with the five `SIZE_MUTANTS`
   (four caught, one equivalent) for image/segment emission, port comparison,
   and a seed promote only after the full gate.
2. **Directory iterator lifetime.** The design returns copies, never borrowed
   names. A future zero-copy optimization needs a language-level invalidation
   story; do not add one silently. Document in `FORK.md`.
3. **Eza behaviour mismatch.** 0.23.3 source is verified, but the release
   binary's build features (xattr, git, locale, colour) can differ. P0 probes
   the binary; the probe wins. The design's E0 subset may shrink if the binary
   contradicts a source-derived rule.
4. **Restricted long is user-visible.** Plain `eza -l` errors; numeric/UTC/
   exact-bytes only. The user must accept this or fund NSS/human sizes/local
   time as a separate feature.
5. **Path semantics and display.** `entryPath`/`paths::join`/operand display
   must match the reference; tests cover operands outside cwd, `../`, trailing
   slash, `//`, `.`, `..`, `/`, whole-parent quoting, and target path
   resolution/follow. The 4096-byte limit and path race remain.
6. **Terminal width/grid.** `--width` makes eza treat redirected output as
   terminal-ish; pty behaviour may be unstable in CI. E2 uses `--width` as the
   primary oracle and pty as optional.
7. **Colour.** Default palette and environment precedence are version- and
   build-specific; E1 records codes and keeps a small subset.
8. **Locale/time/identity.** UTC, numeric ids, C locale only. Tests are stable;
   users outside those assumptions see divergence. Document prominently.
9. **Symlink loops/recursion.** No entry symlink following means no link
   loops; bind mounts can still cycle. `-L` bounds tree depth in E1; no visited
   set in E0/E1. Do not recurse unbounded.
10. **Permission errors and races.** A file can disappear between getdents and
    stat/readlink; eza prints and continues (probe). Tests do not mutate
    concurrently; root skips permission cases.
11. **Memory.** Eza accumulates names and key arrays; long mode accumulates
    per-entry mode/size/mtime/uid arrays. The iterator is bounded (32 KiB
    buffer + 32 KiB chunk + names). The compiler peak must stay under
    2,000,000; the eza 10,000-entry peak is recorded and capped. No GC; leaks
    are checked on success paths.
12. **No floats/no callbacks.** Sizes/times are integers; sorting is keyed.
    If the reference's default human sizes/time cannot be matched with
    integers, the restricted mode is the answer.
13. **Interface stability.** `pub` fields are API. Renaming `Reader.buf`,
    `Stat.mode`, etc. breaks importers; `.sym`/full builds catch it.
14. **Stage 2 surface and code-generation drift.** The CLI/emission rules are
    observed at current main `84558da` (size-fixes merged at `134e6a5`; design
    anchor `6214158`; size-fixed compiler/seed 421,889 bytes, fixed point).
    If a later compiler changes them, build scripts/tests change; module/runtime
    APIs do not. The size fixes change produced executable bytes (zero-tail
    runtime-data segment, `leave; ret` epilogue) but not behaviour;
    `tests/elf_test.py` checks the segment shape and the fixed point is
    seed == obo == stage 2 == stage 3. Use the promoted size-fixed seed, not an
    old Stage 1 or pre-size-fix compiler.
15. **Reference drift.** A different eza version changes defaults, columns,
    quoting, colour and exits. `--require-reference` rejects a different
    version/hash; a bump is its own probe/golden change.
16. **Non-UTF-8.** E0 raw bytes diverge from eza's lossy U+FFFD. Exact cases
    exclude it; E2 implements lossy display. Document until then.
17. **Exit paths.** `exit` skips defers/leaks. Flush stdout and close the
    current Dir before exits where practical; memory gates use success paths.
18. **`--bytes` grouping.** Source suggests plain digits under `LC_ALL=C`,
    but `locale::Numeric` could fall back to English commas on a host. Probe;
    exact size cases stay <1000 except one recorded case.
19. **Host-golden residual.** `-aa -l`, `-aa --sort=size`/`--sort=modified`,
    absolute operands and absolute symlink targets, and `-d ..`/`-d /` are
    excluded from committed goldens;
    if a future test wants them, it must pin the directory and parent metadata
    and record a matching manifest. Numeric-user goldens are
    live/manifest-only. Name-only `-aa` name/type/extension sorts are allowed
    **only with pairwise comparator-distinct names under the comparator used**
    (`natcmpFold` for default/name and lowercase extension; `natcmp` for
    `Name`, `Ext`/`Extension` and type); case-only pairs preserve readdir
    order only under `natcmpFold` (live-only there) while `natcmp`
    distinguishes them, and whitespace-equivalent pairs tie under both and are
    live-only. The `.` extension key is `File::ext(dir.path)` (`None` when the
    operand has no `file_name()`; otherwise `ezaExt` of the final name), not
    the operand component. Do not reintroduce a host-dependent golden by
    accident.
20. **Strict/debug environment.** If `EZA_STRICT`/`EXA_STRICT` or
    `EZA_DEBUG`/`EXA_DEBUG`/`EZA_CONFIG_DIR` is set, eza's parse/log/theme
    behavior differs from E0; tests pin them unset. A user setting them sees
    a documented divergence until strict/debug support lands.

### 12.2 Evidence that would change the design

Revise the design (do not work around) if:

1. **P0 probe contradicts a rule**: `-a`/`-A` counting, dot synthesis/order,
   the `.` extension key (`File::ext(dir.path)`; `None` for `.`/`..`/`/`), default/case/
   natural sort, comparator equality classes and tie order, extension/type
   tie-breaks, reverse ties, whole-parent quoting and the root-parent prefix,
   **lines/default symlink targets and marker placement**,
   **target path resolution/follow (`-d sub/link`, target-is-symlink,
   chain/loop)**, long columns/widths/type chars/`-` sizes, dates/pre-1970,
   exit statuses, permission status, `--bytes` grouping, optional-value
   spellings, bare `--color`, `--time-style` non-long, non-TTY default, `-d`
   alias, `-aa -l`/`-aa --sort=size`/`-aa --sort=modified`/absolute/`-d ..`
   host data.
2. **The pinned binary differs from the v0.23.3 source** because of build
   features (xattr `@`, git columns, locale). Adjust scope/goldens or choose a
   different build.
3. **The current size-fixed main's Stage 2 surface differs from
   `obo/LANGUAGE.md`/`FORK.md`** (CLI, emission, `.sym`, `--check`,
   version/messages): change
   `build_tools.py`, Makefile and tests; module/runtime APIs remain.
4. **The compiler live peak approaches 2,000,000** after P3/P5a/P6a: cut in
   the documented order and stop for the user; never raise the budget.
5. **`Result[[]int]`/`Result[?[]byte]` descriptor ownership fails under
   OBO_CHECK** (leak/double free): stop and fix the descriptor/ok-cell contract
   before building `fs`/eza; do not paper over it in the stdlib.
6. **The 10,000-entry eza memory case exceeds the recorded cap**: free key
   arrays earlier, use parallel arrays, or stream `--sort=none` (not in E0);
   do not weaken the memory gate.
7. **DT_UNKNOWN is returned for every entry** or long symlink targets are
   common: add a stat/readlink cache or accept per-entry cost; revisit only if
   time/memory gates fail.
8. **Long joined paths or path races are common**: move to
   `fstatat(dirfd,name)`/`readlinkat(dirfd,name)` (a new runtime feature), or
   document/restrict.
9. **Methods/interfaces/lambdas land before eza**: a comparator-based sort API
   may replace key arrays; the eza core does not block on them.
10. **The user requires user/group names, human sizes or local time for the
    first eza**: design NSS/passwd, integer human-size formatting and timezone
    support as separate features; do not add floats or libc.
11. **The reference cannot be acquired**: stop and get the user's decision;
    goldens-only is a roadmap stop-condition violation (section 3.2).
12. **PTY behaviour is unstable in CI**: use `--width` as the grid oracle;
    defer real-TTY grid parity until `terminalWidth` and pty tests are stable.

### 12.3 Decisions that must go to the user

1. **Eza reference (top external blocker).** Approve pinning eza 0.23.3
   x86_64 GNU release asset, its sha256, and installing it in CI; or refuse and
   accept the goldens-only downgrade/roadmap violation.
2. **Restricted long.** Accept that `-l` requires `--bytes
   --time-style=long-iso` plus at least one of `-n`/`--no-user`, and that
   plain `eza -l` errors.
3. **Tool location and churn (resolved).** All four tools move to `obo/cmd/`
   with explicit relative imports; the sort tool never imports `sort`. This is
   the design decision, not an open choice. Accept the one-time docs/tests path
   churn (merged main still has `programs/`).
4. **Metadata staging.** E0 only slots 33–36, with isatty/getenv in P5a and
   terminalWidth in P6a (recommended), or one larger metadata feature now.
5. **Non-UTF-8.** Accept E0 exact cases being ASCII/valid UTF-8 and E2 adding
   lossy display, or fund lossy conversion earlier.
6. **`-H/--links`.** Leave it out of E0 (recommended; restricted long has no
   link column) and add in E1 if wanted, or keep it permanently out.
7. **Colour scope.** E1 default palette + `NO_COLOR`; `LS_COLORS`/`EZA_COLORS`
   subset later; long-column colour only if probed (recommended).
8. **Out-of-scope confirmation.** Multiple operands, `-R`, `--sort=none`,
   user/group names, human sizes, local time, icon rendering (`--icons=always`),
   git, xattrs/ACLs, device nodes.
9. **Tool migration churn (confirmation).** The location is resolved (`cmd/`,
   item 3); confirm the one-time docs/tests path churn this creates. No
   alternative location is proposed.
10. **Host-golden restrictions.** Accept that committed goldens exclude
    `-aa -l`, `-aa --sort=size`, `-aa --sort=modified`, absolute operands,
    absolute symlink targets (the valid root-parent cases `/tmp`, `/tmp/`,
    `/`, and the broken `/tar get` case) and `-d ..`/`-d /`; those remain
    live/shape cases. Name-only `-aa` is committed only for name/type/
    extension sorts when every listed pair (including synthetic dot names) is
    comparator-distinct under the comparator used (case-only pairs tie only
    under `natcmpFold`; whitespace-equivalent pairs tie under both and are
    live-only). Extension uses a relative fixture-owned operand; its `.` key
    is `File::ext(dir.path)` — `None` when the operand has no `file_name()`
    (`.`, `..`, `/`), else `ezaExt` of the final name (`d` → None,
    `dx.x` → Some("x"), `dx.` → Some("")). If the
    user wants the excluded cases committed, the fixture directory and
    parent/cwd must be pinned and a manifest maintained.
11. **Harness churn.** Accept the P2 compatibility-function removal together
    with `fmt`/`errors`/`cmd`, the P1 tool-build gate, the P2 re-anchoring of
    `tests/lib`/`mutate_io`, and the new `tests/mutate_meta.py` with its two
    runners and measured counts; without them the tool, library and metadata
    gates would not be honest.

---

## 13. Rejected alternatives

| Alternative | Why rejected |
|---|---|
| Omit symlink targets in default/`-1` lines, or put the classify marker on the link | eza 0.23.3 `lines.rs` always calls `with_link_paths()`; default/`-1`/`-d` show `name -> target` and mark the target. Only grid omits targets and marks the link. |
| Reintroduce a shared tool sym-dir (the old `.obo/sym`) | `build()` calls from tests and parallel `make tools eza` would share it; the merged builder already uses `<out>/sym` per output. Keep it. |
| Run `mutate_io` library mutants against the real `lib/` | The mutated `io.obx` would never be used; build a temporary copied module tree per mutant. |
| Commit `-aa -l`, absolute operands or `-d ..`/`-d /` goldens as host-independent | They print the temp parent's/cwd's mode/mtime/path; exclude them or pin parent/cwd and maintain a manifest. |
| Name the module `args` and require `as cli` everywhere | `args` is a universe builtin; the alias is rejected. `cli` is clearer and has no permanent wart. |
| Byte-order name sort for E0 | eza's default is case-folded natural (`natord`); byte order diverges for `A a B b` and `2 10`. Implement natord. |
| A reverse comparator/flag in `sort` | eza sorts ascending and reverses the whole vector (ties reversed); a comparator reverse preserves ties and diverges. Expose ascending stable orders + `reverse`. |
| Restrict all fixtures instead of implementing quoting | Default eza output quotes spaces/apostrophes; exact parity requires the rule. Implement `QuoteSpaces` and control escaping. |
| Implement the guessed long shape (`mode links uid gid size date`) | Wrong columns/order/type chars; 0.23.3 has perms/size/user/date, `.` regular char, `-` non-regular sizes, no links without `-H`. Probe/source pin the shape. |
| Include `-H` links in restricted long | Links are opt-in in eza; including them changes widths and host-dependent nlink. Leave out E0. |
| GNU-coreutils exit codes (usage 2) | eza uses 3 for options, 1 I/O, 2 input-path metadata, 13 documented permission. Use eza constants and probe. |
| Put `eza.obx` in `programs/` with plain `"sort.obx"` | Resolver finds the importer dir first; it would load the tool root/cycle. Use `cmd/` + explicit `../lib/sort.obx`. |
| Add an unused `sort` import to the sort tool | It is a cycle and unused; remove it. |
| `env::string` helper | Shadows the primitive type/conversion and breaks later `string` declarations. Use `env::value`. |
| P1 replaces the merged `lib/strings.obx` with the §5.1 file | The merged tools call `cmpBytes`/`errText`/`digits`/`appendPadded`; replacing the file breaks `make tools`. P1 edits in place and keeps them until P2 removes them with the tools' migration. |
| Reintroduce a concatenation path alongside modules | Two build paths, stale copies, no `.sym` interfaces. Merged main has no concatenation; keep it deleted. |
| Per-component parent quoting, or a naive `parent + "/" + name` | eza's `add_parent_bits` escapes the whole parent string once and has a root branch: a one-component `/` parent emits a single `/` (target `/tmp` is `/tmp`, not `//tmp`); per-component quoting prints `'x y'/sub/f` instead of `'x y/sub'/f`. |
| Treat `/tar get` as a valid root-parent target | A normal test cannot create files at `/`, so eza reports a symlink to it **Broken**: `'/tar get'`, no marker, even with `-F=always`. The valid root branch is `/tmp` and `/`; the broken branch is a separate live case. |
| Expect a valid `../tar get` target without creating the sibling | `../` resolves against the case directory's parent; with no `tar get` sibling there the metadata lookup fails and the broken branch escapes the whole raw target: `'../tar get'`, no marker even with `-F=always`. Valid whole-parent quoting uses the concrete in-fixture target `a b/target` → `'a b'/target`. |
| Key the synthetic `.` by the operand's last path component | eza's `new_aa_current` passes `dir.path`, and `File::ext` takes the extension of that path's final normal component: `File::ext(dir.path)` — `None` when the path has no `file_name()` (`.`, `..`, `/`), else `ezaExt` of the final name (`d` → None, `dx.x` → Some("x"), `dx.` → Some("")); using the component itself, or the literal `ezaExt(lastComponent(...))`, gives the wrong key for `.`/`..`. |
| Commit `-aa --sort=size`/`--sort=modified` name-only goldens | The dots' sort keys are host directory/parent metadata; those orders are live/shape-only. |
| Assume distinct names never compare Equal, so no tie-break is needed | Natord is not injective: `A`/`a` and `README`/`readme` compare Equal **only under `natcmpFold`** (under `natcmp` they compare `Less`), while `pic4`/`pic 4` and `a`/` a` compare Equal under both. eza's stable `sort_by` preserves readdir order for Equal keys. A committed name/type/extension or `-aa` golden therefore requires pairwise comparator-distinct names under the comparator used; tied cases are live-only. |
| Cut `readLinkPath` to save compiler peak | Default/`-1`/`-d`/long must render `name -> target`; cutting it silently drops E0 symlink targets. Stop and report instead. |
| Runtime-built `Dir`/`Stat` records with descriptors | Descriptor-passing through the 4-argument limit; raw bytes + stdlib parsing reuse `readFd`'s shape. |
| A generic `syscall(n,a,b,c,d)` builtin | Exposes raw pointers/ABI, bypasses ownership/Result descriptors; unsafe and untestable. |
| One `stat` builtin per field | Multiple syscalls per entry. |
| `statInto(path,follow,mut out)` | Mutating builtin + structural 14-field check; defer until measurement. |
| One directory entry per kernel call | getdents64 batches; one syscall per entry is slow. |
| Returning borrowed names/targets | Functions cannot return references; invalidation story missing. Copy. |
| Handles for entries/iterators | Unnecessary; a local owner with a clear lifetime is simpler. |
| Floats for sizes/times/width | No float type; integer math and restricted modes cover eza E0. |
| Generic comparator sort | No function values/lambdas/generics; keyed stable orders cover eza. |
| Stdlib monolith | Coarse dependencies/effects, wide rebuilds; leaf modules with `pub` faces. |
| GNU ls as the eza oracle | Different grid, quoting, columns, colour and defaults. |
| A Python model of eza | Duplicates the tool and is not independent; reference/goldens are the oracle. |
| Vendor an eza binary in the repo | Size, licensing, supply chain, drift. Download outside the repo and verify hashes. |
| `getpwuid`/`getgrgid`/NSS or `/etc/passwd` in E0 | Host-dependent, not a byte oracle; use `-n`/`--no-user`. |
| Local timezone/TZif in E0/E1 | Complex, version-dependent; UTC `long-iso` with `TZ=UTC`. |
| Full Unicode width table first | Large/versioned; ASCII width for E0/E1; curated UTF-8 in E2. |
| Full `LS_COLORS` first | Version/precedence-dependent; default palette + `NO_COLOR` first. |
| Tree/grid before names/long | E0's deterministic non-TTY path is the foundation; grid needs width/pty. |
| Multiple operands/`-R`/`--sort=none` in E0 | Directory order is not a stable oracle; per-directory headers and recursion add scope. Reject clearly. |
| `currentDir` in E0 | No E0 use; it would add a result helper/slot for nothing. Drop. |
| Directory `st_size` in long output | eza renders `-` for non-regular sizes; using st_size is wrong and host-dependent. |
| Treat `-t` as sort-by-time | `-t/--time` selects the displayed timestamp field; sorting is `--sort=modified`. Reject in E0. |
| Icons/colour in E0 | Non-TTY E0 has none; E1/E2. |

---

## 14. Appendices

### Appendix A — natord-plus-plus 2.0.0 (ASCII form)

Reference: crate `natord-plus-plus 2.0.0`, `lib.rs` (`compare_iter`,
`compare`, `compare_ignore_case`), the dependency of eza 0.23.3. `compare`
skips Unicode whitespace (`char::is_whitespace`), compares decimal digit runs
naturally and other chars by `char::cmp`. `compare_ignore_case` lowercases each
char via `to_lowercase()` before the same loop. The E0 ASCII implementation
uses `isSpace` (space, `0x09..0x0D`), bytes `0'..9`, and ASCII `A..Z` folding;
non-ASCII bytes compare by value (divergence documented; E2 decodes).

Pseudocode (`fold` selects ASCII case folding; digits unaffected):

```
fn natcmp(a, b, fold):
    i = 0; j = 0
    loop:
        while i < len(a) && isSpace(a[i]): i += 1
        while j < len(b) && isSpace(b[j]): j += 1
        if i == len(a) && j == len(b): return 0
        if i == len(a): return -1
        if j == len(b): return 1

        da = isDigit(a[i]); db = isDigit(b[j])
        if da && db:
            if a[i] == '0' || b[j] == '0':
                # left-aligned matching: 015 < 12
                if a[i] != b[j]: return sign(a[i], b[j])
                i += 1; j += 1
                while i < len(a) && j < len(b) && isDigit(a[i]) && isDigit(b[j]):
                    if a[i] != b[j]: return sign(a[i], b[j])
                    i += 1; j += 1
                if i < len(a) && isDigit(a[i]): return 1
                if j < len(b) && isDigit(b[j]): return -1
                # both runs ended: continue the outer loop
            else:
                # right-aligned matching: 15 < 123
                last = 0
                while i < len(a) && j < len(b) && isDigit(a[i]) && isDigit(b[j]):
                    if last == 0 && a[i] != b[j]: last = sign(a[i], b[j])
                    i += 1; j += 1
                if i < len(a) && isDigit(a[i]): return 1
                if j < len(b) && isDigit(b[j]): return -1
                if last != 0: return last
                # both runs ended equal: continue
        else:
            ca = fold ? lowerAscii(a[i]) : a[i]
            cb = fold ? lowerAscii(b[j]) : b[j]
            if ca < cb: return -1
            if ca > cb: return 1
            i += 1; j += 1
```

Notes: whitespace is skipped only at the outer loop, never inside a digit run;
a digit run ends at the first non-digit; the leading-zero branch is
left-aligned and the nonzero branch right-aligned; when both runs end, the
outer loop continues without re-reading. The crate's test orders:

```
["a","a0","a1","a1a","a1b","a2","a10","a20"]
["x2-g8","x2-y7","x2-y8","x8-y8"]
["1.001","1.002","1.010","1.02","1.1","1.3"]
["1-02","1-2","1-20","10-20","fred","jane","pic1","pic2","pic2a","pic3",
 "pic4","pic4   alpha","pic 4 else","pic4  last","pic5","pic5.07",
 "pic5.08","pic5.13","pic5.113","pic 5 something","pic 6","pic   7",
 "pic100","pic100a","pic120","pic121","pic2000","tom","x2-g8","x2-y7",
 "x2-y8","x8-y8"]
```

Add E0 orders: under fold `A == a < B == b`, so a **stable sort** of
`[A,a,B,b]` keeps `A,a,B,b`; this is stable-sort output, not a comparator
chain (`compare_ignore_case("A","a")` returns Equal). `natcmp`
(case-sensitive) gives `A < B < a < b`. Also `2 < 10`, `01 < 1`, `0 < 00`,
`rfc1 < rfc2 < rfc10`. Equality classes must carry their comparator:
`A`/`a` and `README`/`readme` are Equal **only under `natcmpFold`**;
case-sensitive `natcmp` gives `Less` for both pairs
(`natcmp("A","a") = -1`, `natcmp("README","readme") = -1`). `pic4`/`pic 4`
and `a`/` a` compare Equal under **both** comparators (whitespace is skipped
in both). All four are equality-class tests, never a strict-order assertion.

### Appendix B — quoting/escaping table

`quoteName(name, true)` (E0 default). `\t`, `\n`, `\r` are actual control
bytes in the input; shown escaped here for readability.

| input | output |
|---|---|
| `plain` | `plain` |
| `a b` | `'a b'` |
| `it's` | `"it's"` |
| `a b'c` | `"a b'c"` |
| `a"b c` | `'a"b c'` |
| `a"b'c d` | `"a"b'c d"` (embedded `"` not escaped) |
| `back\slash` | `back\slash` |
| `tab<TAB>here` | `tab\there` |
| `line<LF>feed` | `line\nfeed` |
| `x<ESC>y` | `x\u{1by}` |
| `del<DEL>` | `del\u{7f}` |
| `<TAB>` | `\t` |
| `sp ace<ESC>` | `'sp ace\u{1b}'` |
| `-dash` | `-dash` |
| `255-byte...` | unchanged |

`quoteName(name, false)` (`--no-quotes`, E1): no quote characters; spaces and
apostrophes raw; controls still escaped. Symlink target names always use
`quoteName(target, true)`, even if the outer style is `false`; the target's
parent prefix uses the outer style. Classify markers follow the closing quote:
`'a b'*`, `link -> 'tar get'/`.

**Symlink display in lines/default/`-1`/`-d` (and long/tree):**

| link name | target | output without `-F` | output with `--classify=always` |
|---|---|---|---|
| `link` | regular `target` | `link -> target` | `link -> target` (no marker) |
| `link` | directory `dir` | `link -> dir` | `link -> dir/` |
| `link` | executable `prog` | `link -> prog` | `link -> prog*` |
| `link` | `tar get` | `link -> 'tar get'` | `link -> 'tar get'` (no marker) |
| `link` | dangling `missing` | `link -> missing` | `link -> missing` (no marker) |
| `link` | unreadable/error | `link` | `link` (no arrow/marker) |
| entry `link` | `b`, where `b` is a symlink to a dir | `link -> b` | `link -> b/` (followed stat) |
| entry `link` | `b`, chain/loop `b -> link` | `link -> b` (Broken) | `link -> b` (no marker) |
| `-d sub/link` | executable `sub/target` | `sub/link -> target` | `sub/link -> target*` |
| `-d sub/link` | directory `sub/dir` | `sub/link -> dir` | `sub/link -> dir/` |
| `link` (live) | absolute `/tmp` | `link -> /tmp` | `link -> /tmp/` |
| `link` (live) | absolute `/` | `link -> /` | `link -> //` (target plus marker) |
| `link` (live) | absolute `/tar get` (Broken) | `link -> '/tar get'` | `link -> '/tar get'` (no marker) |
| grid E2 | any | `link` | `link@` (marker on the link) |

No `-F` still shows the target; `-F` only adds the marker. The link name itself
is escaped with the outer style; the target name uses `QuoteSpaces`;
classification/broken detection uses stat (follow) on the resolved target path
(§7.9).

**Parent prefixes are escaped once as a whole string** (operand and target),
via `strings::parentPrefix`, which has `add_parent_bits`' root branch: a
one-component `/` parent emits a single `/` and no second separator.

| case | output |
|---|---|
| operand `/tmp/f` | `/tmp/f` (not `//tmp/f`) |
| operand `x y/sub/f` | `'x y/sub'/f` |
| `-d 'x y/sub/link'` | `'x y/sub'/link` |
| operand `x y/sub/link -> target` | `'x y/sub'/link -> target` |
| entry `link -> a b/target` | `link -> 'a b'/target` |
| entry in dir, `link -> ../tar get` (Broken: no sibling `tar get` in the case directory's parent, asserted absent) | `link -> '../tar get'`; no marker even with `-F=always` |
| entry `link -> /tmp` (live) | `link -> /tmp`; with `-F=always` `link -> /tmp/` |
| entry `link -> /` (live) | `link -> /`; with `-F=always` `link -> //` |
| entry `link -> /tar get` (live, Broken) | `link -> '/tar get'`; no marker even with `-F=always` (not a root-parent case) |

### Appendix C — restricted long line grammar

```
perms  = 10 chars from fmt::modeString (eza type char + 9 permission bits)
         (+ "@" if xattrs, not in E0)
size   = decimal st_size for regular files; "-" otherwise (E0 no devices)
         right-aligned to max(size cell widths), then one space
user   = numeric uid (decimal), left-aligned to max(uid digit widths), then
         one space; omitted with --no-user
date   = "YYYY-MM-DD HH:MM" UTC, or "-" for negative/unrepresentable mtime;
         left-aligned to max(date widths), then one space
name   = quoteName(name, true) + optional classify marker
         + optional " -> " + quoted/escaped target (+ target marker)
```

- One space after every table cell; the name is not padded.
- Widths are computed over the displayed entries after filter+sort.
- `-d` uses the operand's own cells.
- Examples in section 7.8. P0 confirms every byte; if the probe differs,
  change this grammar, not the fixtures.

Cell examples:

| entry | perms | size | user | date | name (no marker) |
|---|---|---|---|---|---|
| regular 0, 0644 | `.rw-r--r--` | `0` | `1000` | `2020-01-02 03:04` | `a` |
| regular 1234, 0755 | `.rwxr-xr-x` | `1234` | `1000` | `2020-01-02 03:04` | `b` |
| directory 0755 | `drwxr-xr-x` | `-` | `1000` | `2020-01-02 03:04` | `d` |
| symlink 0777 | `lrwxrwxrwx` | `-` | `1000` | `2020-01-02 03:04` | `l -> target` |
| FIFO 0644 | `\|rw-r--r--` | `-` | `1000` | `2020-01-02 03:04` | `f` |
| socket 0755 | `srwxr-xr-x` | `-` | `1000` | `2020-01-02 03:04` | `s` |
| setuid 4755 | `.rwsr-xr-x` | `7` | `1000` | `2020-01-02 03:04` | `su` |
| setgid 2755 | `.rwxr-sr-x` | `7` | `1000` | `2020-01-02 03:04` | `sg` |
| sticky dir 1777 | `drwxrwxrwt` | `-` | `1000` | `2020-01-02 03:04` | `st` |
| pre-1970 mtime | `.rw-r--r--` | `3` | `1000` | `-` | `old` |

### Appendix D — source reference index

Repository at current main `84558da` (size-fixes merged at `134e6a5`, seed
promoted at `84558da`; design anchor `6214158`, tools-modules merged at
`4e733a6`). Source 10,448 lines / 382,329 bytes (sha256 `44770afa…`);
`seed/obo` == `obo` == 421,889 bytes (sha256 `55dab101…`); fixed point
seed == obo == stage 2 == stage 3.

- `obo/src/obo.obo` — runtime slot constants 0–32 (`RT_CLOSE_FD = 32`),
  `universe` builtin words, `rtCall` ≤4 arguments, `resultCall3`/`resultCall4`,
  `pinStr`, `nestedExpr`, import cycle/alias checks, `resolveSource`
  importer-first, `OBO_VERSION = "0.2"`, `SYM_FORMAT = 1`, `SYM_ABI = 1`; the
  size-fix names `dataFileLen`, the runtime-data `phdr`/copy site and
  `epilogue` (`leave`; `ret`). (Line numbers are stale after the Stage 2/tools
  and size-fix changes; use the named declarations: `rtCall`, `dataFileLen`,
  `epilogue`, and the `words(...)` list in `universe`.)
- `obo/runtime/entry.S` — slot/check sequences.
- `obo/runtime/runtime.c` — `sys3`, `rt_init` env scan, `cpath`,
  `err_cell`/`ok_cell`, `openFile`/`readFd` patterns.
- `obo/tools/build_tools.py` — direct module-root build; private `<out>/sym`;
  `_map_error` path shortening; single `build(names,out,obo,rt)`;
  `LIB_MODULES`; no concatenation.
- `obo/lib/{io,strings}.obx` — merged `module io`/`module strings` libraries.
- `obo/programs/{cat,wc,sort}.obx` — module-root tools (P2 moves them to
  `cmd/`).
- `obo/tests/` merged gates: `tools.py`, `io_test.py`, `run_fork.py`,
  `elf_test.py`, `port_compare.py`, `mutate_io.py`, `mutate_port.py`;
  `io_test`/`run_fork`/`port_compare`/`mutate_io` are already module-aware;
  `elf_test.py` is the runtime-data/`leave; ret` image gate.
- `obo/tests/{module_test.py,sym_test.py,sym_fuzz.py}` — Stage 1/2 module and
  symbol-file gates; private sym-dirs.
- `obo/tests/mutate_fork.py` — 527/549 caught + 22 equivalent, including the
  five `SIZE_MUTANTS` (`image: the runtime data keeps its zero tail`,
  `image: the runtime data memory size is its file size`,
  `image: the runtime data copies only its first byte`, the equivalent old
  two-instruction epilogue, and the `int3` return).
- `obo/tests/run/{io_reader,io_writer}.obx` — module roots importing `io.obx`.
- `obo/tests/lib/{boundaries,stream,short_write,write_padded}.obx` — library
  roots; `write_padded` uses `io::writePadded` until P2.
- `obo/Makefile` — `tools`/`tools-check`/`tools-memory`; builder-created
  `build/tools/sym`; P2/P4 add `eza` and `build/eza/sym`.
- `obo/LANGUAGE.md` section 6 — Stage 2 CLI, `.sym` rules, errors.
- `obo/FORK.md` Stage 2, tools-modules and compiler size-fix records —
  versions, tests, memory, build deferral.
- Current baseline: source 10,448 lines / 382,329 bytes (sha256 `44770afa…`);
  compiler and seed 421,889 bytes (sha256 `55dab101…`, fixed point
  seed == obo == stage 2 == stage 3); peak 1,636,176 live / 35,076,608
  allocated, 363,824 spare, 0.11 s; 1,130 tests; 527/549 + 22 mutants;
  495 port mutants; 1,147/1,147 `port_compare` (1,025/1,025 with
  `OBO_REF=seed/obo`); 27 I/O mutants; 27 symbol-file tests; 200 fuzz
  mutations; 45 doc examples; 199 tool checks + 2 memory; `tests/elf_test.py`;
  tool binaries 36,865 / 40,961 / 45,057; minimal 28,673.

Eza v0.23.3 source (downloaded tarball; read, not run):

- `src/options/flags.rs` — flag definitions, `WHEN`, `SORTS`, aliases.
- `src/options/parser.rs` — optional-value parsing, clusters, errors.
- `src/options/filter.rs` — `SortField::deduce`, `DotFilter::deduce`.
- `src/options/file_name.rs` — `Classify::deduce`, `ShowIcons`.
- `src/options/view.rs` — `Mode::deduce`, `TerminalWidth`, `TimeFormat`,
  `SizeFormat`, `UserFormat`, columns.
- `src/options/dir_action.rs` — `-d`/`--list-dirs` handling, conflicts.
- `src/options/theme.rs` — `NO_COLOR` default and explicit override.
- `src/options/version.rs`, `build.rs` — release version string.
- `src/fs/dir.rs` — dot synthesis/filter, read_dir.
- `src/fs/file.rs` — name lossy, ext, type, metadata, size/length, mtime.
- `src/fs/filter.rs` — sort/compare, reverse, group dirs.
- `src/fs/fields.rs` — Type order.
- `src/output/lines.rs` — lines view always calls `with_link_paths()`; default/
  `-1` symlink targets.
- `src/output/escape.rs` — quoting/escaping.
- `src/output/file_name.rs` — name/target/classify rendering, operand parent.
- `src/output/grid.rs` — grid view (no targets; marker on link).
- `src/output/table.rs` — columns, alignment, widths, separators.
- `src/output/render/{size,users,permissions,filetype,times}.rs` — cells.
- `src/output/time.rs` — long-iso and None formatting.
- `src/output/mod.rs` — `TerminalWidth::actual_terminal_width`.
- `src/options/mod.rs` — `EZA_STRICT`/`EXA_STRICT` strictness.
- `src/options/vars.rs` — strict/debug/config/icon/git environment names.
- `src/main.rs` — exit constants/paths, permission branch, input metadata 2.

Reference asset: `https://github.com/eza-community/eza/releases/download/v0.23.3/eza_x86_64-unknown-linux-gnu.tar.gz`
sha256 `02e818f7b06f2c637b25761731104f1e9d6ea8f6e5924ab05eae69db4ed072a5`;
binary sha256 `10457dde35808517d09bcd3bfb6bc386000d06c2126e34b9071e09470a8f7cd8`.

Crates: `natord-plus-plus 2.0.0` (`lib.rs`), `locale 0.2.2`
(`Numeric::format_int`, Linux `newlocale`/`langinfo`).

### Appendix E — file ownership and staging

| Stage | Files | Owner/notes |
|---|---|---|
| P0 | `obo/tests/eza/{VERSION,REFERENCE.md}`, fixture generator, `tests/eza.py` skeleton, goldens | new files; no compiler/runtime |
| P1 | `lib/strings.obx` (edit in place, add §5.1, keep compatibility), `lib/{paths,sort,time,cli}.obx`, `tests/stdlib.py`, `tests/stdlib/`, `tests/imports.py` | runs on current size-fixed main (Stage 2 surface); owns `strings`; no compiler/Makefile/tool-source changes; gate includes the tools build |
| P2 | `lib/strings.obx` (remove compatibility), `lib/io.obx` (remove `writePadded`, add `writeByte`), `lib/fmt.obx`, `lib/errors.obx`, `cmd/{cat,wc,sort}.obx`, `tools/build_tools.py` `SRCDIR` and stale `programs/` docstring/error text, `tests/lib/write_padded.obx` (add `fmt`, keep `io`), `tests/mutate_io.py` patterns, `tests/io_test.py` docstring/paths, `Makefile` `programs/*.obx` comment, docs | after P1; no `.obo` delete/rename; no compiler change; `mutate_io` stays 27/27 with the stated writeByte/fmt/errors boundary |
| P3 | `src/obo.obo` (slots 33–36, `sys4`, builtins; `OBO_VERSION` 0.3), `runtime/{runtime.c,entry.S}`, `lib/fs.obx`, `tests/rt_meta_test.c`, fs-level `tests/meta/*`, `tests/mutate_meta.py` (mutated-runtime and mutated-compiler runners), compiler cases, `tests/sym_test.py` version constants, docs | after P2, on the size-fixed seed `84558da` (421,889 bytes); full gate baseline 1,130/527/549+22/495/486/45/`elf_test`/sym 27/200 plus measured additions; promote |
| P4 | `cmd/eza.obx`, `tests/eza.py` E0 cases (including `-d sub/link`, target-is-symlink, chain/loop, live root-target `/tmp`/`/` cases and the broken `/tar get` case), goldens/memory | after P3 seed; no compiler change |
| P5a | `src/obo.obo` (slots 37/38; version bump), runtime, `lib/env.obx`, `io::isTty`, tests, `tests/mutate_meta.py` extension (both runners), `tests/sym_test.py` version constants | compiler feature; full gate (§11.2 baseline plus measured additions) + promote |
| P5b | `cmd/eza.obx` colour/tree, `tests/eza.py` cases, docs | after P5a seed |
| P6a | `src/obo.obo` (slot 39; version bump), runtime, `io::termWidth`, tests, `tests/mutate_meta.py` extension (both runners), `tests/sym_test.py` version constants | only if grid needs it; full gate (§11.2 baseline plus measured additions) + promote |
| P6b | `cmd/eza.obx` grid/UTF-8, tests | after P6a |

Merge order: P0/P1 parallel (P1 additive on current size-fixed main); P2 after P1; P3
serial compiler after P2 and the size-fixed seed `84558da`; P4 after P3; P5a compiler; P5b;
P6a; P6b. Never run a compiler feature concurrently with another compiler
feature.

### Appendix F — verified vs probe checklist

| Item | Status |
|---|---|
| Repo facts (slots 0–32, rtCall ≤4, resultCall3/4, no resultCall2/sys4, import resolution, merged module libraries/tools and non-concatenating builder, Stage 2 CLI/.sym, size-fixed compiler/seed 421,889 bytes with fixed point, peak 1,636,176, merged gate figures incl. `run_fork` 1,130, `elf_test`, mutate_fork 527/549 + 22, mutate_port 495/495, `sym_test` 27/27 and `sym_fuzz` 200/200) | Verified at main `84558da` (size-fixes merged at `134e6a5`; design anchor `6214158`, tools-modules merge `4e733a6`) |
| Modules Stage 1/2 syntax/semantics; `.sym` reader/writer; `--check`/`--sym-dir`/`--emit-sym`/`--version`; exact messages | Verified from `obo/LANGUAGE.md` section 6, `obo/FORK.md` Stage 2 record and merged tests |
| eza v0.23.3 release asset, tarball/binary sha256, version string source | Verified by download/hash/read; the independent v4 review, v5 re-review and v6 review ran the binary in several throw-away probes; this design pass did not run it. `--version` is `v0.23.3 [+git]`. |
| eza flag set, `-a` count, `-A`, `-d` canonical+alias, optional values and `split_on_equals` empty-equals/interspersed parsing, sort spellings (**no `default` value**), natord dependency and **equality classes** (`A`/`a`, `README`/`readme`, `pic4`/`pic 4`, `a`/` a`), stable-sort/readdir tie rule, quoting rule and **whole-parent escape with the root branch**, **lines/default symlink targets and marker-on-target**, **target path resolution/follow (stat) vs link lstat**, **operand existence by lstat**, **`.` extension key `File::ext(dir.path)`**, long columns/type chars/size `-`, grid marker-on-link, exit constants/source-2, non-TTY Lines, NO_COLOR default/override, icons default/bare, `--color` bare, `--time-style` non-long, strict/debug/config env names, operand display, -D/-f, `-aa` size/modified key source, non-UTF-8 lossy | Source-verified; independent-review throw-away-probe observations in §3.3a; P0 re-records |
| Exact eza output bytes for every case; errors/stderr; permission status; `--bytes` grouping under `LC_ALL=C`; `-F automatic` operand behaviour; `-d` trailing-slash display; **`-d sub/link` target resolution, target-is-symlink, chain/loop with `-F=always`**; **tie order for `A`/`a`, `README`/`readme`, `pic4`/`pic 4`, `a`/` a`**; **`-aa --sort=extension d`/`dx.x`/operand-`.` dot keys**; **root-parent targets `/tmp`, `/tmp/`, `/` with/without `-F=always`, the broken absolute `/tar get` target (no marker, not a root-parent case), and the broken relative `../tar get` target (`'../tar get'`, no sibling, no marker)**; spaced-parent operands/targets; `-aa -l`/`-aa --sort=size`/`-aa --sort=modified`/absolute/`-d ..` host data; xattr `@`; colour SGRs; tree glyphs; grid layout; pty width; `--width` auto interaction; DT_UNKNOWN filesystem | **Probe (P0)**; §3.3a has the independent reviews' throw-away-probe observations to re-record, not commit |
| Compiler peak after new branches; runtime text growth; tool/eza binary sizes/peaks | Current baseline 1,636,176 live / 35,076,608 allocated / 363,824 spare; unmeasured for the new branches; measure, do not guess |
| Stat offsets | Design for x86-64 Linux; C harness in P3 |
| Raw `dirfd` metadata (`fstatat`/`readlinkat`) | Not in this design; future fix for path limit/race |
| Full Unicode width/case folding | E2 curated; full parity not assumed |

---

End of design note v7. Nothing in this note modifies the repository. Baseline
figures are current main `84558da` (size-fixes merged at `134e6a5`; seed
promoted; design anchor `6214158`). The next action is the user's decision on
section 12.3, first the eza reference acquisition/pin. The v4/v5 review
findings remain applied; the v6 review's two blocking findings (the
`../tar get` broken-target fixture and the unknown-name reporting rule) and
its precision notes are applied in v7; §3.3a records the independent reviews'
throw-away-probe observations for P0 to re-record.




