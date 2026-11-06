# E3 probes: the long view (-l, -lh)

Probes against the reference eza 0.23.3 built with cargo (`cargo install eza
--version 0.23.3`, binary in scratch). Run with `env -i`, `LC_ALL=C`,
`TZ=UTC` unless a probe says otherwise. These are the facts the long view in
`cmd/eza.obx` implements; the goldens under `golden/ref/long/` pin them.

## Binary identity

- `eza --version` of the cargo build matches VERSION (0.23.3).
- Its sha256 is 6f07b27fe2c47f857ac8dc8e242b439cc970f1abc8c4c3750634081a68cbdebc,
  not the pinned 10457dde35808517d09bcd3bfb6bc386000d06c2126e34b9071e09470a8f7cd8.
- With `--reference-exe` the cargo build reproduced every committed reference
  golden that existed before this change: 553 of 554 cases passed. The one
  failure was `errors/own_long_restricted`, an own golden of the old
  restricted `-l`, which is removed.
- Goldens recorded in this change carry the cargo sha256 (the harness notes
  the difference). Re-record with the pinned binary when it is available.

## Sizes

Decimal (default), binary (`-b`) and bytes (`-B`), checked on 1,902 files of
sizes from 0 to 10^13 and on the boundaries 999, 1000, 1023, 1024, 1049, 1050,
1250, 9949, 9950, 9999, 999949, 999950 and 1048576. The rules:

- below the base: the plain number, colour tier 0 (green);
- below 10 of the prefix: one decimal, rounded from the binary value of the
  quotient, ties to even (`%.1f`): 1050 bytes is "1.1k", 1250 is "1.2k";
- 10 and above: the whole part, rounded up from one half: 9950 is "10k";
- the prefix tops out at E (T and above are "huge", purple).

Colours (the default gradient): bytes green, kilo bold green, mega yellow,
giga red, huge purple. A size of a directory or other non-file is "-".

## Dates

Local zone (TZ, else /etc/localtime, else UTC; lib/tz.obx). For a file whose
year equals the current year (in that zone): `%e %b %H:%M`, for example
" 1 Nov 10:20". Otherwise `%e %b  %Y` with the year in five columns:
"15 Jun  2025". Pre-1970 modification times show "-". long-iso is
`2020-09-13 12:26`. Zones checked: UTC, Europe/London, America/New_York,
Australia/Sydney, Asia/Kolkata.

The current-year form depends on the date the run happens. The goldens use
the 2020 fixtures, so they pass in any year; the current-year form is
checked only by live comparison (`user_names`, live only).

## Header (-h)

Labels "Permissions", "Size", "User", "Date Modified", "Name", underlined
(`\e[4m`) when colour is on. Column widths: permissions 11 (10 plus a space),
size max(4, widest), user max(4, widest), date 13 (12 in the cells, padded).
No header row when the listing has no entries (an empty directory prints
nothing). A single-file operand has a header.

## Colours

The default theme (eza's `default_theme.rs`) with nu-ansi-term's difference
painting, which the painter in `cmd/eza.obx` already does:

- type: directory bold blue, symlink cyan, pipe/special yellow, block and
  character bold yellow, socket bold red, regular file plain;
- rwx: user read bold yellow, user write bold red, user execute bold green
  (underlined for a regular file), group and other read yellow, write red,
  execute green (normal weight);
- set bits (s, S, t, T): purple;
- dash: bold dark grey (1;90);
- user name: bold yellow when the uid is the process's real uid (from
  /proc/self/status), else plain;
- date: blue (34), including "-";
- header labels: underline.

The LS_COLORS and EZA_COLORS overrides are not applied to the long view.

## Not done

- `--time-style=iso`, `full-iso`, `relative` and `+FORMAT` are rejected; the
  reference accepts them.
- `-g`, `-H`, `-i`, `-S`, `-@`, `-Z`, `-o`, `--total-size`, the other time
  fields (`-t`, `-u`, `-U`), `--changed` and git columns are not implemented.
- With `-T` and `-h` the reference draws the header and widens the columns
  the same way; this build does too (checked on `-T -lh -n`).
