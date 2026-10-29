# eza P0 probe report

Produced by `obo/tests/eza/probe.py` against the authorized pinned
reference. Every byte below is observed output; skipped probes have no
output. Temporary harness paths are normalized to `<root>`; probe
arguments show the reference as `<ref>`, while the report header and
host manifest record its absolute path and sha256. Absolute-target and
host-metadata probes are live/shape-only and are not committed goldens
(design section 9.4).

Reference: ` /tmp/obo-stdlib-v2-eza-src/eza `

## Host manifest

- platform: ` Linux-6.18.54.1-microsoft-standard-WSL2-x86_64-with-glibc2.39 `
- kernel: ` 6.18.54.1-microsoft-standard-WSL2 `
- machine: ` x86_64 `
- python: ` 3.12.3 `
- euid: ` 1000 `
- egid: ` 1000 `
- umask: ` 0022 `
- filesystem: ` ext4 `
- environment pin: ` PATH=/usr/bin:/bin LC_ALL=C LANG=C LANGUAGE=C TZ=UTC; HOME/TMPDIR per probe; all section 9.5 variables unset unless set `
- stdin: ` /dev/null unless the probe supplies stdin bytes (group 18) `
- reference path: ` /tmp/obo-stdlib-v2-eza-src/eza `
- reference sha256: ` 10457dde35808517d09bcd3bfb6bc386000d06c2126e34b9071e09470a8f7cd8 `
- fixture capabilities: ` {'euid': 1000, 'umask': 18, 'pre_epoch': True, 'xattr': True, 'invalid_utf8': True, 'fifo': True, 'socket': True, 'hardlink': True, 'symlink': True, 'deep_path': True, 'nonroot': True, 'dev_full': True, 'ambient_xattr': False} `
- dt-unknown candidates: ` {'/proc': {'entries': 101, 'unknown': 0, 'sample': []}, '/sys': {'entries': 12, 'unknown': 0, 'sample': []}, '/dev/shm': {'entries': 7, 'unknown': 0, 'sample': []}, '/run': {'entries': 34, 'unknown': 0, 'sample': []}} `

## 1. Version, help and binary identity

### version

- cwd: ` <root> `
- argv: ` ['<ref>', '--version'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` b894fb9eff57c3c7df417034b49889a878c20fa8e21470da6ebdb79113bd7c0e `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'eza - A modern, maintained replacement for ls\nv0.23.3 [+git]\nhttps://github.com/eza-community/eza\n'
```

- stderr bytes:

```python
b''
```

- note: ` the exact pinned release output (three lines) `

### version_short

- cwd: ` <root> `
- argv: ` ['<ref>', '-V'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 87f6eab2a2e1e6aeb0caf8a008b10f4e96ee3a8b0518b956c6c107264c18c0ba `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Unknown argument -V\n'
```

### help

- cwd: ` <root> `
- argv: ` ['<ref>', '--help'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 097874002fd6596ce14fd0a0e25e2e9a7d5287839993c77e7a23f26e10cf7930 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"Usage:\n  eza [options] [files...]\n\nMETA OPTIONS\n  -?, --help                 show list of command-line options\n  -v, --version              show version of eza\n\nDISPLAY OPTIONS\n  -1, --oneline              display one entry per line\n  -l, --long                 display extended file metadata as a table\n  -G, --grid                 display entries as a grid (default)\n  -x, --across               sort the grid across, rather than downwards\n  -R, --recurse              recurse into directories\n  -T, --tree                 recurse into directories as a tree\n  -X, --dereference          dereference symbolic links when displaying information\n  -F, --classify=WHEN        display type indicator by file names (always, auto, never)\n  --colo[u]r=WHEN            when to use terminal colours (always, auto, never)\n  --colo[u]r-scale           highlight levels of 'field' distinctly(all, age, size)\n  --colo[u]r-scale-mode      use gradient or fixed colors in --color-scale (fixed, gradient)\n  --icons=WHEN               when to display icons (always, auto, never)\n  --no-quotes                don't quote file names with spaces\n  --hyperlink                display entries as hyperlinks\n  --absolute                 display entries with their absolute path (on, follow, off)\n  --follow-symlinks          drill down into symbolic links that point to directories\n  -w, --width COLS           set screen width in columns\n\n\nFILTERING AND SORTING OPTIONS\n  -a, --all                  show hidden and 'dot' files. Use this twice to also\n                             show the '.' and '..' directories\n  -A, --almost-all           equivalent to --all; included for compatibility with `ls -A`\n  -d, --treat-dirs-as-files  list directories as files; don't list their contents\n  -D, --only-dirs            list only directories\n  -f, --only-files           list only files\n  --show-symlinks            explicitly show symbolic links (for use with --only-dirs | --only-files)\n  --no-symlinks              do not show symbolic links\n  -L, --level DEPTH          limit the depth of recursion\n  -r, --reverse              reverse the sort order\n  -s, --sort SORT_FIELD      which field to sort by\n  --group-directories-first  list directories before other files\n  --group-directories-last   list directories after other files\n  -I, --ignore-glob GLOBS    glob patterns (pipe-separated) of files to ignore\n  --git-ignore               ignore files mentioned in '.gitignore'\n  Valid sort fields:         name, Name, extension, Extension, size, type,\n                             created, modified, accessed, changed, inode, and none.\n                             date, time, old, and new all refer to modified.\n\nLONG VIEW OPTIONS\n  -b, --binary               list file sizes with binary prefixes\n  -B, --bytes                list file sizes in bytes, without any prefixes\n  -g, --group                list each file's group\n  --smart-group              only show group if it has a different name from owner\n  -h, --header               add a header row to each column\n  -H, --links                list each file's number of hard links\n  -i, --inode                list each file's inode number\n  -M, --mounts               show mount details (Linux and Mac only)\n  -n, --numeric              list numeric user and group IDs\n  -O, --flags                list file flags (Mac, BSD, and Windows only)\n  -S, --blocksize            show size of allocated file system blocks\n  -t, --time FIELD           which timestamp field to list (modified, accessed, created)\n  -m, --modified             use the modified timestamp field\n  -u, --accessed             use the accessed timestamp field\n  -U, --created              use the created timestamp field\n  --changed                  use the changed timestamp field\n  --time-style               how to format timestamps (default, iso, long-iso,\n                             full-iso, relative, or a custom style '+<FORMAT>'\n                             like '+%Y-%m-%d %H:%M')\n  --total-size               show the size of a directory as the size of all\n                             files and directories inside (unix only)\n  -o, --octal-permissions    list each file's permission in octal format\n  --no-permissions           suppress the permissions field\n  --no-filesize              suppress the filesize field\n  --no-user                  suppress the user field\n  --no-time                  suppress the time field\n  --stdin                    read file names from stdin, one per line or other separator \n                             specified in environment\n  --git                      list each file's Git status, if tracked or ignored\n  --no-git                   suppress Git status (always overrides --git,\n                             --git-repos, --git-repos-no-status)\n  --git-repos                list root of git-tree status\n  --git-repos-no-status      list each git-repos branch name (much faster)\n    \n  -@, --extended             list each file's extended attributes and sizes\n  -Z, --context              list each file's security context\n"
```

- stderr bytes:

```python
b''
```

- note: ` complete help bytes `

### help_short

- cwd: ` <root> `
- argv: ` ['<ref>', '-h'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b''
```

## 2. Default non-TTY output

### empty

- cwd: ` <root> `
- argv: ` ['<ref>', 'empty'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b''
```

### one

- cwd: ` <root> `
- argv: ` ['<ref>', 'one'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 092fcfbbcfca3b5be7ae1b5e58538e92c35ab273ae13664fed0d67484c8e78a6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'f\n'
```

- stderr bytes:

```python
b''
```

### onedir

- cwd: ` <root> `
- argv: ` ['<ref>', 'onedir'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` a9294fcd1dbc598ec49a7879ba2d0702c9bf1ba7a0fe2d7881707cbbda36f50b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'sub\n'
```

- stderr bytes:

```python
b''
```

### mixed

- cwd: ` <root> `
- argv: ` ['<ref>', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ``lines mode; symlinks render `name -> target```

### mixed_dot_operand

- cwd: ` <root>/mixed `
- argv: ` ['<ref>', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### mixed_no_operand

- cwd: ` <root>/mixed `
- argv: ` ['<ref>'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b''
```

- note: ` no operand with non-TTY stdin: stdin file-name mode with an empty list, not a cwd listing; see the stdin group and the P0 finding on design section 7.2 `

### one_lines

- cwd: ` <root> `
- argv: ` ['<ref>', '-1', 'one'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 092fcfbbcfca3b5be7ae1b5e58538e92c35ab273ae13664fed0d67484c8e78a6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'f\n'
```

- stderr bytes:

```python
b''
```

### mixed_lines

- cwd: ` <root> `
- argv: ` ['<ref>', '-1', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### mixed_lines_dot

- cwd: ` <root>/mixed `
- argv: ` ['<ref>', '-1', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### links

- cwd: ` <root> `
- argv: ` ['<ref>', 'links'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 116258220cc42782b0b3a32d943609990d8c749121bd6e4fbfb4559c0b671e69 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"b -> real\nchain -> realfile\ndangling -> nowhere\nexec -> exec-target\nexec-target\nfile\nloop1 -> loop2\nloop2 -> loop1\nreal -> realdir\nrealdir\nrealdirlink -> realdir\nrealfile -> file\nspaced -> 'tar get'\n'tar get'\nto_realdirlink -> realdirlink\n"
```

- stderr bytes:

```python
b''
```

### absolute

- cwd: ` <root> `
- argv: ` ['<ref>', 'absolute'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 84fb52c4a8039dae80f054da514cf90c58c84e9f2c53e8e78d8c72839eccfe2d `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"root -> /\nspaceabs -> '/tar get'\ntmp -> /tmp\ntmp_slash -> /tmp\n"
```

- stderr bytes:

```python
b''
```

- note: ` live-only absolute targets: /tmp, /tmp/, /, /tar get `

## 3. `-a`/`-aa`/`-A` and synthesized dots

### a_empty

- cwd: ` <root> `
- argv: ` ['<ref>', '-a', 'empty'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b''
```

### a_one

- cwd: ` <root> `
- argv: ` ['<ref>', '-a', 'one'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 092fcfbbcfca3b5be7ae1b5e58538e92c35ab273ae13664fed0d67484c8e78a6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'f\n'
```

- stderr bytes:

```python
b''
```

### a_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-a', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5fb71556086c067d8b765d26cfac13da2c2075ea09d6299f2359593e25154b4a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n..double\n.hidden\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### aa_empty

- cwd: ` <root> `
- argv: ` ['<ref>', '-aa', 'empty'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 31dce20d34ba3eff4d30b98516a8a4c00752b64e10a540dfd3d1127b518a172c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.\n..\n'
```

- stderr bytes:

```python
b''
```

### aa_one

- cwd: ` <root> `
- argv: ` ['<ref>', '-aa', 'one'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 20931c22de4a722a08382b48acf4b454140fabd8858797eebf25e19db012524b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.\n..\nf\n'
```

- stderr bytes:

```python
b''
```

### aa_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-aa', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f04c4a3e0281eb456556a9a345f2cdb4aa2b44ffb67c2169f3a8fb2bbbd41569 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n.\n..\n..double\n.hidden\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### A_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-A', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5fb71556086c067d8b765d26cfac13da2c2075ea09d6299f2359593e25154b4a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n..double\n.hidden\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### A_a_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-A', '-a', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5fb71556086c067d8b765d26cfac13da2c2075ea09d6299f2359593e25154b4a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n..double\n.hidden\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### A_aa_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-A', '-aa', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f04c4a3e0281eb456556a9a345f2cdb4aa2b44ffb67c2169f3a8fb2bbbd41569 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n.\n..\n..double\n.hidden\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### all_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '--all', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5fb71556086c067d8b765d26cfac13da2c2075ea09d6299f2359593e25154b4a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n..double\n.hidden\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### all_all_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '--all', '--all', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f04c4a3e0281eb456556a9a345f2cdb4aa2b44ffb67c2169f3a8fb2bbbd41569 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n.\n..\n..double\n.hidden\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### a_repeat_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-a', '-a', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f04c4a3e0281eb456556a9a345f2cdb4aa2b44ffb67c2169f3a8fb2bbbd41569 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n.\n..\n..double\n.hidden\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### aa_cwd_mixed

- cwd: ` <root>/mixed `
- argv: ` ['<ref>', '-aa', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f04c4a3e0281eb456556a9a345f2cdb4aa2b44ffb67c2169f3a8fb2bbbd41569 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n.\n..\n..double\n.hidden\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` -aa on the cwd; explicit . operand (no-operand is stdin mode) `

### aa_name_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-aa', '--sort=name', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f04c4a3e0281eb456556a9a345f2cdb4aa2b44ffb67c2169f3a8fb2bbbd41569 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n.\n..\n..double\n.hidden\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` name-only -aa; synthetic dots are comparator-distinct `

### aa_type_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-aa', '--sort=type', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f02ac2917c94fc5411e75595c03c1d3571f30a25b7305ae329b1fe73ded57a04 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b".\n..\ndir\nsub.dir\n-\n--x\n..double\n.hidden\nBeta\nalpha\ngamma.txt\nnoexec\nprog\n'tar get'\nbroken -> missing\ndirlink -> dir\nlink -> alpha\nspaced -> 'tar get'\npipe\nsock\n"
```

- stderr bytes:

```python
b''
```

### aa_ext_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-aa', '--sort=extension', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 49d4380b780097f84e15a3fbe7623d47b0dfb0a6e2a0c4ad6d5469c3e33cb4c0 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n.\n..\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\n'tar get'\nsub.dir\n..double\n.hidden\ngamma.txt\n"
```

- stderr bytes:

```python
b''
```

### ext_key_d

- cwd: ` <root>/ext `
- argv: ` ['<ref>', '-aa', '--sort=extension', 'd'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 382a1f7751c8d367870fbc406495fcfdc6ced93c6e2a3879cd6c80adde8dc32e `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.\n..\nb\nsub\na.txt\n'
```

- stderr bytes:

```python
b''
```

- note: ` dot key File::ext("d") = None `

### ext_key_dx_x

- cwd: ` <root>/ext `
- argv: ` ['<ref>', '-aa', '--sort=extension', 'dx.x'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` ae0213f28a6bf231da3f65bef703b195b52428e57c8589403ecc09c54d782b6d `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'..\nb\nsub\na.txt\n.\n'
```

- stderr bytes:

```python
b''
```

- note: ` dot key File::ext("dx.x") = Some("x") `

### ext_key_dx_dot

- cwd: ` <root>/ext `
- argv: ` ['<ref>', '-aa', '--sort=extension', 'dx.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` c6692782af62d91d6e627d9b6cecbb3568a4aedbe9bed6bff4ce84d6e9383eab `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'..\nb\nsub\n.\na.txt\n'
```

- stderr bytes:

```python
b''
```

- note: ` dot key File::ext("dx.") = Some("") `

### ext_key_dot

- cwd: ` <root>/ext `
- argv: ` ['<ref>', '-aa', '--sort=extension', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` c070bb4c9354f382d4f670a3546977cad3c00a4e4c957bf6008da9d717bea0d0 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.\n..\nb\nd\nnoext\nsub\ndot.\ndx.\n.git\nb.tar.gz\na.txt\nsame.txt\nZ.TXT\ndx.x\n'
```

- stderr bytes:

```python
b''
```

- note: ` dot key File::ext(".") = None `

### ext_key_dotdot

- cwd: ` <root>/ext `
- argv: ` ['<ref>', '-aa', '--sort=extension', '..'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` c59371117dbab74fc819f5dd10d4597c75a56af0fd13a46ad72968ca2ee65a98 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.\n..\nabsolute\nempty\next\ngrid1\ngrid2\ngrid10\ngrid100\ngridlong10\ngridlong100\nhard\ninvalid\nlinks\nlocked\nlong\nmany\nmixed\nmodes\nnames\none\nonedir\noperands\nquoting\nsizes\ntimes\ntree\nxattr\n'
```

- stderr bytes:

```python
b''
```

- note: ` dot key File::ext("..") = None `

### aa_long_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-aa', '-l', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2b40a7ea3faa66e431be8d5165b07833aa96d4d9da2fa79ec0c53f5155f58b6b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b".rw-r--r--  5 chris 13 Sep  2020 -\n.rw-r--r--  9 chris 13 Sep  2020 --x\ndrwxr-xr-x  - chris 13 Sep  2020 .\ndrwxr-xr-x  - chris 13 Sep  2020 ..\n.rw-r--r--  7 chris 13 Sep  2020 ..double\n.rw-r--r--  7 chris 13 Sep  2020 .hidden\n.rw-r--r--  6 chris 13 Sep  2020 alpha\n.rw-r--r--  5 chris 13 Sep  2020 Beta\nlrwxrwxrwx  - chris 13 Sep  2020 broken -> missing\ndrwxr-xr-x  - chris 13 Sep  2020 dir\nlrwxrwxrwx  - chris 13 Sep  2020 dirlink -> dir\n.rw-r--r--  6 chris 13 Sep  2020 gamma.txt\nlrwxrwxrwx  - chris 13 Sep  2020 link -> alpha\n.rw-r--r--  3 chris 13 Sep  2020 noexec\n|rw-r--r--  - chris 13 Sep  2020 pipe\n.rwxr-xr-x 17 chris 13 Sep  2020 prog\nsrw-r--r--  - chris 13 Sep  2020 sock\nlrwxrwxrwx  - chris 13 Sep  2020 spaced -> 'tar get'\ndrwxr-xr-x  - chris 13 Sep  2020 sub.dir\n.rw-r--r--  7 chris 13 Sep  2020 'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` live/shape-only: dot rows use host parent metadata `

### aa_long_one

- cwd: ` <root> `
- argv: ` ['<ref>', '-aa', '-l', 'one'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` cb1c391e72412c99f1fa21857f520ff7a96f0e8758ad17e6867ce5b43bac5d9d `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'drwxr-xr-x - chris 13 Sep  2020 .\ndrwxr-xr-x - chris 13 Sep  2020 ..\n.rw-r--r-- 2 chris 13 Sep  2020 f\n'
```

- stderr bytes:

```python
b''
```

- note: ` live/shape-only: dot rows use host parent metadata `

### aa_size_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-aa', '--sort=size', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` ed82feb884aec40a909d76d77966530690b5f48cffeaff7e73d863ab8218cdef `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"pipe\nsock\ndirlink -> dir\nnoexec\nlink -> alpha\n-\nBeta\ngamma.txt\nalpha\nspaced -> 'tar get'\n.hidden\n'tar get'\nbroken -> missing\n..double\n--x\nprog\n.\n..\ndir\nsub.dir\n"
```

- stderr bytes:

```python
b''
```

- note: ` live/shape-only: dot sort keys are host st_size `

### aa_modified_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-aa', '--sort=modified', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e9f3044a8a28c416d3e0081404fce330fd476e391b39d9014720972eed2d5757 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b".\n..\nlink -> alpha\nspaced -> 'tar get'\ngamma.txt\n-\nBeta\npipe\nsock\ndirlink -> dir\nnoexec\n.hidden\n'tar get'\nalpha\n--x\nprog\ndir\nbroken -> missing\nsub.dir\n..double\n"
```

- stderr bytes:

```python
b''
```

- note: ` live/shape-only: dot sort keys use host parent mtime `

## 4. `-d`/`--treat-dirs-as-files`/`--list-dirs` and operands

### d_file

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-d', 'f'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 092fcfbbcfca3b5be7ae1b5e58538e92c35ab273ae13664fed0d67484c8e78a6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'f\n'
```

- stderr bytes:

```python
b''
```

### d_dir

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-d', 'dir'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` baa36e7060b5155d6e766266c2424ddbe8e56fdb38ab3bfb76cd6351b0889606 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'dir\n'
```

- stderr bytes:

```python
b''
```

### d_symlink

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-d', 'l'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 556dd11e5fda56d99f0f0a2b40e79caf7d7af884d08165c7fce7fe4b5a5b2a5d `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'l -> f\n'
```

- stderr bytes:

```python
b''
```

### d_dangling

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-d', 'broken'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 7b64e27b63ad4e5eee6c3c71e90710fb645af51386ea018a6d57907f67ab0c4c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'broken -> missing\n'
```

- stderr bytes:

```python
b''
```

### treat_file

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '--treat-dirs-as-files', 'f'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 092fcfbbcfca3b5be7ae1b5e58538e92c35ab273ae13664fed0d67484c8e78a6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'f\n'
```

- stderr bytes:

```python
b''
```

### treat_dir

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '--treat-dirs-as-files', 'dir'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` baa36e7060b5155d6e766266c2424ddbe8e56fdb38ab3bfb76cd6351b0889606 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'dir\n'
```

- stderr bytes:

```python
b''
```

### list_dirs_dir

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '--list-dirs', 'dir'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` baa36e7060b5155d6e766266c2424ddbe8e56fdb38ab3bfb76cd6351b0889606 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'dir\n'
```

- stderr bytes:

```python
b''
```

### list_dirs_symlink

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '--list-dirs', 'dl'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` a6ec46a54f0bb9ed47e21ad27301848a1624390bd1530cea3d5d826dc82b99bb `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'dl -> dir\n'
```

- stderr bytes:

```python
b''
```

### d_long_file

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-l', '-d', 'f'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` a43b41859f3d02ad086444cbbbd101d9fa1357c080b6b4500a5e5308923a8942 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 chris 13 Sep  2020 f\n'
```

- stderr bytes:

```python
b''
```

### d_long_dir

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-l', '-d', 'dir'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 60a50b027148b5fe316db90333a0febda7a8d9ded23f4408eeeaeff79a4c6882 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'drwxr-xr-x - chris 13 Sep  2020 dir\n'
```

- stderr bytes:

```python
b''
```

### d_long_symlink

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-l', '-d', 'l'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 55e0428269c84192663c6c2f01cc04bc0149dfd57129da989b79e5c6458d2ee2 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'lrwxrwxrwx - chris 13 Sep  2020 l -> f\n'
```

- stderr bytes:

```python
b''
```

### d_long_dangling

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-l', '-d', 'broken'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 7cb10fbd84bdf6dd818ea5bdac45d6c627c9b080cf709bd1d715b814e41624e8 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'lrwxrwxrwx - chris 13 Sep  2020 broken -> missing\n'
```

- stderr bytes:

```python
b''
```

### operand_dir_slash

- cwd: ` <root>/operands `
- argv: ` ['<ref>', 'dir/'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b''
```

### operand_symlink_dir_slash

- cwd: ` <root>/operands `
- argv: ` ['<ref>', 'dl/'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b''
```

### operand_double_slash

- cwd: ` <root>/operands `
- argv: ` ['<ref>', 'sub//f'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5e69e97ef2a0caa3c6394c1f76da194a30113f2f076c641c712e4fc7f34269ca `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'sub/f\n'
```

- stderr bytes:

```python
b''
```

### operand_dot_slash

- cwd: ` <root>/operands `
- argv: ` ['<ref>', 'sub/./f'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5e69e97ef2a0caa3c6394c1f76da194a30113f2f076c641c712e4fc7f34269ca `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'sub/f\n'
```

- stderr bytes:

```python
b''
```

### operand_dot_file

- cwd: ` <root>/operands `
- argv: ` ['<ref>', './f'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` a35a3be6cae2b16ec144e5fdb50e02f90d8a1b7a5e7db94b7983964e9e0e0bf0 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'./f\n'
```

- stderr bytes:

```python
b''
```

### operand_dot

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` b6be61b5a51e26e22f39e62442627a8758d52a6152ea662ecff34aaca8b5569b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"broken -> missing\ndir\ndl -> dir\nf\ng\nl -> f\nsub\ntarget\n'x y'\n"
```

- stderr bytes:

```python
b''
```

### operand_dotdot

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '..'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 640c3448dd63ccff90072a752e291c220df26cab10cbb4cb438a58d6d2fa515c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'absolute\nempty\next\ngrid1\ngrid2\ngrid10\ngrid100\ngridlong10\ngridlong100\nhard\ninvalid\nlinks\nlocked\nlong\nmany\nmixed\nmodes\nnames\none\nonedir\noperands\nquoting\nsizes\ntimes\ntree\nxattr\n'
```

- stderr bytes:

```python
b''
```

### spaced_parent_file

- cwd: ` <root>/operands `
- argv: ` ['<ref>', 'x y/sub/f'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 099d18ced715c01d8b62c80b2185391229bc53a5c6efa252dd867b832c3508d0 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"'x y/sub'/f\n"
```

- stderr bytes:

```python
b''
```

### spaced_parent_dir

- cwd: ` <root>/operands `
- argv: ` ['<ref>', 'x y/sub/'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 1524f3cbe16e4d9e5d326193e69863f22a3e8b1974d1a98501c117da8fe85ac5 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'f\nlink -> target\ntarget\n'
```

- stderr bytes:

```python
b''
```

### spaced_parent_link_d

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-d', 'x y/sub/link'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 1685cb266a0164a1259a5b3875c29246a2c42d9c811656367bed0e21e92f293e `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"'x y/sub'/link -> target\n"
```

- stderr bytes:

```python
b''
```

### d_sub_link

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-d', 'sub/link'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 4d419847722414529aa3b6fff8a666dcfd659f1392e40b955abe1e2dd8a9dcd0 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'sub/link -> target\n'
```

- stderr bytes:

```python
b''
```

### d_sub_dirlink

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-d', 'sub/dirlink'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` b785d02a195dff9c04be09d610c46c146c767743a5ef99132c6b6355f5dd2fbe `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'sub/dirlink -> real\n'
```

- stderr bytes:

```python
b''
```

### d_root

- cwd: ` <root> `
- argv: ` ['<ref>', '-d', '/'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f465c3739385890c221dff1a05e578c6cae0d0430e46996d319db7439f884336 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'/\n'
```

- stderr bytes:

```python
b''
```

- note: ` live-only operand display / `

## 5. Default name order and comparator ties

### names_default

- cwd: ` <root> `
- argv: ` ['<ref>', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### names_all

- cwd: ` <root> `
- argv: ` ['<ref>', '-a', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` bac00f78233e56386be30a386e6b818af9a0edf1e29e33ff9426edf17aacb032 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n.hidden\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### names_reverse

- cwd: ` <root> `
- argv: ` ['<ref>', '-r', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 295611734c51530d3615cdd5070060e7bb9784dd4ce16fce56ddaade7bd102b2 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"~\nZ\nz\nx-\nUPPER\nunder_score\ntie_space\ntie_readme\ntie_pic\ntie_dot\ntie_Aa\nrfc10\nrfc2\nrfc1\nreadme\nREADME\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\n'pic 4'\npic4\nordered\ndot.\nB\nb\n'a b'\na\nA\n' a'\n10\n2\n1\n01\n001\n' .'\n-x\n--x\n--\n-\n"
```

- stderr bytes:

```python
b''
```

### names_Name

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=Name', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 725be0a4685b737916e121d991459de42c91a6ab23e3d3694a1efceafbb5c9ca `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\nA\nB\nREADME\nUPPER\nZ\na\n' a'\n'a b'\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nx-\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### names_lower_name

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=name', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### names_filename

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=filename', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### names_dot_name

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=.name', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### tie_Aa

- cwd: ` <root>/names/tie_Aa `
- argv: ` ['<ref>', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2fa9938a05497ce89297338ea11764cf46defe36d7d7bae32f964ac843e31cc1 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a\nA\n'
```

- stderr bytes:

```python
b''
```

- note: ` live tie: A/a under natcmpFold; readdir-order sensitive `

### tie_readme

- cwd: ` <root>/names/tie_readme `
- argv: ` ['<ref>', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 94cfbe8b9a4c9e8dd1729054e19cbb625dcddc4ba3557aa174c28a51d497f776 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'readme\nREADME\n'
```

- stderr bytes:

```python
b''
```

- note: ` live tie: README/readme under natcmpFold `

### tie_pic

- cwd: ` <root>/names/tie_pic `
- argv: ` ['<ref>', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 9a4fe679b5035e66bc096d6f108d0243eb2f6631691123002d1fcabe45d17171 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"'pic 4'\npic4\n"
```

- stderr bytes:

```python
b''
```

- note: ` live tie: pic4/pic 4 under both comparators `

### tie_space

- cwd: ` <root>/names/tie_space `
- argv: ` ['<ref>', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` d995675be6f30a3306a29e011797ab10cf5fb649ef07cfce7771b5e090824b24 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"a\n' a'\n"
```

- stderr bytes:

```python
b''
```

- note: ` live tie: a/ a under both comparators `

### tie_dot

- cwd: ` <root>/names/tie_dot `
- argv: ` ['<ref>', '-aa', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 1a4b152d3763fcecb62d17c5709509f9e31aaec784aaf13f44237768aef58016 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b".\n' .'\n..\n"
```

- stderr bytes:

```python
b''
```

- note: ``live tie: real ` .` beside synthetic .``

### group_dirs_first

- cwd: ` <root> `
- argv: ` ['<ref>', '--group-directories-first', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` eaa03dd3550d1875de8f3d9553b981171fe46629458143e774b3a1fa5804e482 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"dir\ndirlink -> dir\nsub.dir\n-\n--x\nalpha\nBeta\nbroken -> missing\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### group_dirs_first_reverse

- cwd: ` <root> `
- argv: ` ['<ref>', '--group-directories-first', '-r', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 10f2cba12b8988984cf693369b321c5a34188cad1f0fc29483a216784e1d5a48 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"sub.dir\ndirlink -> dir\ndir\n'tar get'\nspaced -> 'tar get'\nsock\nprog\npipe\nnoexec\nlink -> alpha\ngamma.txt\nbroken -> missing\nBeta\nalpha\n--x\n-\n"
```

- stderr bytes:

```python
b''
```

### D_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-D', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` ccb56a05b01e76a4c45e641feaed8a7160f56b11695326192947a92373761da9 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'dir\nsub.dir\n'
```

- stderr bytes:

```python
b''
```

### f_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-f', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 706a20d93ab569e0ef8a9ea59e5f8f4cb91603228a52ddd5ee3aff19d110c0eb `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\ngamma.txt\nnoexec\nprog\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### D_f_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-D', '-f', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### D_reverse_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-D', '-r', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 9fb91148e81325b6766a7e9b715272a4623a2f7e3ec6992de093a9755d93fdd1 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'sub.dir\ndir\n'
```

- stderr bytes:

```python
b''
```

## 6. Explicit sort keys

### size

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=size', 'sizes'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` eb89b7318ec347172d8317c30d635fb6471257d5024d149b84b0746b05fb7cce `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b's000\ns001\ns999\ns1000\ns1024\ns1025\ns4095\ns4096\ns1m\nsparse\n'
```

- stderr bytes:

```python
b''
```

### size_reverse

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=size', '-r', 'sizes'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` ad7167e49be022aa15572d3d18d4d0a27e4cfd52ae1add03ad9171a27853e437 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'sparse\ns1m\ns4096\ns4095\ns1025\ns1024\ns1000\ns999\ns001\ns000\n'
```

- stderr bytes:

```python
b''
```

### size_name_only_aa

- cwd: ` <root> `
- argv: ` ['<ref>', '-aa', '--sort=size', 'sizes'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5fce47a65d57f7263d457fe2e81003b8049aa3d97caa9df450742b21630e7347 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b's000\ns001\ns999\ns1000\ns1024\ns1025\ns4095\n.\n..\ns4096\ns1m\nsparse\n'
```

- stderr bytes:

```python
b''
```

- note: ` live/shape-only: dot keys are host metadata `

### modified

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=modified', 'times'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 79f63fef97b06ba9229b5cedfc2ec7201469049b8143db4b9d6f4afb0fb88ae2 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tpre\nt1970\nt1999\nt2000\nt2020\nt2038\nt2100\n'
```

- stderr bytes:

```python
b''
```

### time

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=time', 'times'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 79f63fef97b06ba9229b5cedfc2ec7201469049b8143db4b9d6f4afb0fb88ae2 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tpre\nt1970\nt1999\nt2000\nt2020\nt2038\nt2100\n'
```

- stderr bytes:

```python
b''
```

### modified_reverse

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=modified', '-r', 'times'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` cbba5844e9be301841d22069d164c44a192ea86b95322e170809047af4f12055 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b't2100\nt2038\nt2020\nt2000\nt1999\nt1970\ntpre\n'
```

- stderr bytes:

```python
b''
```

### modified_name_only_aa

- cwd: ` <root> `
- argv: ` ['<ref>', '-aa', '--sort=modified', 'times'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` dcb1b50fb545edcd9ee80cee04662075a424e831f9d36b0378a080f8c95cab8f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tpre\nt1970\nt1999\nt2000\nt2020\n.\n..\nt2038\nt2100\n'
```

- stderr bytes:

```python
b''
```

- note: ` live/shape-only: dot keys are host metadata `

### ext_lower

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=ext', 'ext'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2f02a0d1f789ca141335757bf47bd536d1e2a948dfe06804a9e6da89f292dafd `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'b\nd\nnoext\nsub\ndot.\ndx.\nb.tar.gz\na.txt\nsame.txt\nZ.TXT\ndx.x\n'
```

- stderr bytes:

```python
b''
```

### extension_lower

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=extension', 'ext'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2f02a0d1f789ca141335757bf47bd536d1e2a948dfe06804a9e6da89f292dafd `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'b\nd\nnoext\nsub\ndot.\ndx.\nb.tar.gz\na.txt\nsame.txt\nZ.TXT\ndx.x\n'
```

- stderr bytes:

```python
b''
```

### Ext

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=Ext', 'ext'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` fd7b8a5780d9613b3a0894070dc2f3b530405c758e97e4678a2097b4343c7db8 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'b\nd\nnoext\nsub\ndot.\ndx.\nb.tar.gz\nZ.TXT\na.txt\nsame.txt\ndx.x\n'
```

- stderr bytes:

```python
b''
```

### Extension

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=Extension', 'ext'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` fd7b8a5780d9613b3a0894070dc2f3b530405c758e97e4678a2097b4343c7db8 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'b\nd\nnoext\nsub\ndot.\ndx.\nb.tar.gz\nZ.TXT\na.txt\nsame.txt\ndx.x\n'
```

- stderr bytes:

```python
b''
```

### extension_reverse

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=extension', '-r', 'ext'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 448039c18a521340fb601dc8c2ed40153d5d300c809e429c810ba9a6e76106b7 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'dx.x\nZ.TXT\nsame.txt\na.txt\nb.tar.gz\ndx.\ndot.\nsub\nnoext\nd\nb\n'
```

- stderr bytes:

```python
b''
```

### type

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=type', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 7c4efb789f6684a4e42916cbc802e769e1695c3090c7d65e01b4f85ce01ec8dd `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"dir\nsub.dir\n-\n--x\nBeta\nalpha\ngamma.txt\nnoexec\nprog\n'tar get'\nbroken -> missing\ndirlink -> dir\nlink -> alpha\nspaced -> 'tar get'\npipe\nsock\n"
```

- stderr bytes:

```python
b''
```

### type_reverse

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=type', '-r', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` d6f3f7b5d7647099c24c6b6b5e2296db2e972569d88cb9fc2f80f78f0f444e52 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"sock\npipe\nspaced -> 'tar get'\nlink -> alpha\ndirlink -> dir\nbroken -> missing\n'tar get'\nprog\nnoexec\ngamma.txt\nalpha\nBeta\n--x\n-\nsub.dir\ndir\n"
```

- stderr bytes:

```python
b''
```

### name_uppercase

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=Name', '-r', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 3a9f7bf96288c6aa004a34741bab90d571b84ff684076064aa23320d4667ca3c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"~\nz\nx-\nunder_score\ntie_space\ntie_readme\ntie_pic\ntie_dot\ntie_Aa\nrfc10\nrfc2\nrfc1\nreadme\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\n'pic 4'\npic4\nordered\ndot.\nb\n'a b'\na\n' a'\nZ\nUPPER\nREADME\nB\nA\n10\n2\n1\n01\n001\n' .'\n-x\n--x\n--\n-\n"
```

- stderr bytes:

```python
b''
```

### none

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=none', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 15652da2845d28e129c05ea64e9ca4894f582cb85f9c7ba0ca4d53a090861b5c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"link -> alpha\nspaced -> 'tar get'\ngamma.txt\n-\nBeta\npipe\nsock\ndirlink -> dir\nnoexec\n'tar get'\nalpha\n--x\nprog\ndir\nbroken -> missing\nsub.dir\n"
```

- stderr bytes:

```python
b''
```

- note: ` out of E0 scope; recorded for completeness `

### inode

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=inode', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 19a032809252eab25621225c6077ec98bedcfb5c4109fc3f795a3b021d2343ce `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"alpha\nBeta\ngamma.txt\ndir\nsub.dir\nprog\nnoexec\npipe\nsock\n-\n--x\n'tar get'\nlink -> alpha\ndirlink -> dir\nbroken -> missing\nspaced -> 'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` host-dependent inode order `

### date_alias

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=date', 'times'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 79f63fef97b06ba9229b5cedfc2ec7201469049b8143db4b9d6f4afb0fb88ae2 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tpre\nt1970\nt1999\nt2000\nt2020\nt2038\nt2100\n'
```

- stderr bytes:

```python
b''
```

### newest_alias

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=newest', 'times'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 79f63fef97b06ba9229b5cedfc2ec7201469049b8143db4b9d6f4afb0fb88ae2 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tpre\nt1970\nt1999\nt2000\nt2020\nt2038\nt2100\n'
```

- stderr bytes:

```python
b''
```

## 7. Quoting and symlink targets

### quoting_default

- cwd: ` <root> `
- argv: ` ['<ref>', 'quoting'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f6fb6db7f2c8215ba924a1aee4a0833177fffce58a8fa2a13a0920164ff2dd96 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\' \'\n\'a b\'\n"apos\'trophe"\nback\\slash\n"both \' and ""\nbrokenrel -> \'../tar get\'\n\'broken spaced\' -> \'a b/missing\'\ncr\\rname\nctl\\u{1}name\n\'ctl target\' -> ctl\\u{1}target\ndel\\u{7f}name\ndquote"name\nesc\\u{1b}name\n\'lead space\'\nnl\\nname\nplain\ntab\\tname\n\'trail \'\n\'valid spaced\' -> \'a b\'/target\n\'with space\'\n'
```

- stderr bytes:

```python
b''
```

### quoting_lines

- cwd: ` <root> `
- argv: ` ['<ref>', '-1', 'quoting'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f6fb6db7f2c8215ba924a1aee4a0833177fffce58a8fa2a13a0920164ff2dd96 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\' \'\n\'a b\'\n"apos\'trophe"\nback\\slash\n"both \' and ""\nbrokenrel -> \'../tar get\'\n\'broken spaced\' -> \'a b/missing\'\ncr\\rname\nctl\\u{1}name\n\'ctl target\' -> ctl\\u{1}target\ndel\\u{7f}name\ndquote"name\nesc\\u{1b}name\n\'lead space\'\nnl\\nname\nplain\ntab\\tname\n\'trail \'\n\'valid spaced\' -> \'a b\'/target\n\'with space\'\n'
```

- stderr bytes:

```python
b''
```

### quoting_long

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', 'quoting'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 809aecfd2730ff573cddf65639bffdeb46fd63a14378079ae683ff81680d7fb9 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 chris 13 Sep  2020 \' \'\ndrwxr-xr-x - chris 13 Sep  2020 \'a b\'\n.rw-r--r-- 2 chris 13 Sep  2020 "apos\'trophe"\n.rw-r--r-- 2 chris 13 Sep  2020 back\\slash\n.rw-r--r-- 2 chris 13 Sep  2020 "both \' and ""\nlrwxrwxrwx - chris 13 Sep  2020 brokenrel -> \'../tar get\'\nlrwxrwxrwx - chris 13 Sep  2020 \'broken spaced\' -> \'a b/missing\'\n.rw-r--r-- 2 chris 13 Sep  2020 cr\\rname\n.rw-r--r-- 2 chris 13 Sep  2020 ctl\\u{1}name\nlrwxrwxrwx - chris 13 Sep  2020 \'ctl target\' -> ctl\\u{1}target\n.rw-r--r-- 2 chris 13 Sep  2020 del\\u{7f}name\n.rw-r--r-- 2 chris 13 Sep  2020 dquote"name\n.rw-r--r-- 2 chris 13 Sep  2020 esc\\u{1b}name\n.rw-r--r-- 2 chris 13 Sep  2020 \'lead space\'\n.rw-r--r-- 2 chris 13 Sep  2020 nl\\nname\n.rw-r--r-- 2 chris 13 Sep  2020 plain\n.rw-r--r-- 2 chris 13 Sep  2020 tab\\tname\n.rw-r--r-- 2 chris 13 Sep  2020 \'trail \'\nlrwxrwxrwx - chris 13 Sep  2020 \'valid spaced\' -> \'a b\'/target\n.rw-r--r-- 2 chris 13 Sep  2020 \'with space\'\n'
```

- stderr bytes:

```python
b''
```

### quoting_no_quotes

- cwd: ` <root> `
- argv: ` ['<ref>', '--no-quotes', 'quoting'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 1b505ebc83d16f3b9315c4cb57ddb2fdfcf4f58f80c1ff735cc27f58c0119db1 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b' \na b\napos\'trophe\nback\\slash\nboth \' and "\nbrokenrel -> ../tar get\nbroken spaced -> a b/missing\ncr\\rname\nctl\\u{1}name\nctl target -> ctl\\u{1}target\ndel\\u{7f}name\ndquote"name\nesc\\u{1b}name\nlead space\nnl\\nname\nplain\ntab\\tname\ntrail \nvalid spaced -> a b/target\nwith space\n'
```

- stderr bytes:

```python
b''
```

### quoting_classify

- cwd: ` <root> `
- argv: ` ['<ref>', '-F=always', 'quoting'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 0c37d954ea68a7885d2d4e1ac983eac926bb9becd36cd3e4de35b8ea874ac619 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\' \'\n\'a b\'/\n"apos\'trophe"\nback\\slash\n"both \' and ""\nbrokenrel -> \'../tar get\'\n\'broken spaced\' -> \'a b/missing\'\ncr\\rname\nctl\\u{1}name\n\'ctl target\' -> ctl\\u{1}target\ndel\\u{7f}name\ndquote"name\nesc\\u{1b}name\n\'lead space\'\nnl\\nname\nplain\ntab\\tname\n\'trail \'\n\'valid spaced\' -> \'a b\'/target\n\'with space\'\n'
```

- stderr bytes:

```python
b''
```

### spaced_operand

- cwd: ` <root> `
- argv: ` ['<ref>', 'quoting/with space'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 36254010796cb097d184840949439878e3355437270c0c45dc9c6fa919736b83 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"quoting/'with space'\n"
```

- stderr bytes:

```python
b''
```

- note: ` operand parent quoting `

### spaced_symlink_operand

- cwd: ` <root> `
- argv: ` ['<ref>', 'quoting/valid spaced'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` ef6c4f1479eb44e0cfab7bdbf4dcc26ed4b6e4c1b2966426d367ee8ceb8b7fa7 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"quoting/'valid spaced' -> 'a b'/target\n"
```

- stderr bytes:

```python
b''
```

### links_spaced_target

- cwd: ` <root> `
- argv: ` ['<ref>', '-1', 'links'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 116258220cc42782b0b3a32d943609990d8c749121bd6e4fbfb4559c0b671e69 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"b -> real\nchain -> realfile\ndangling -> nowhere\nexec -> exec-target\nexec-target\nfile\nloop1 -> loop2\nloop2 -> loop1\nreal -> realdir\nrealdir\nrealdirlink -> realdir\nrealfile -> file\nspaced -> 'tar get'\n'tar get'\nto_realdirlink -> realdirlink\n"
```

- stderr bytes:

```python
b''
```

### ctl_target

- cwd: ` <root> `
- argv: ` ['<ref>', '-1', 'quoting'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f6fb6db7f2c8215ba924a1aee4a0833177fffce58a8fa2a13a0920164ff2dd96 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\' \'\n\'a b\'\n"apos\'trophe"\nback\\slash\n"both \' and ""\nbrokenrel -> \'../tar get\'\n\'broken spaced\' -> \'a b/missing\'\ncr\\rname\nctl\\u{1}name\n\'ctl target\' -> ctl\\u{1}target\ndel\\u{7f}name\ndquote"name\nesc\\u{1b}name\n\'lead space\'\nnl\\nname\nplain\ntab\\tname\n\'trail \'\n\'valid spaced\' -> \'a b\'/target\n\'with space\'\n'
```

- stderr bytes:

```python
b''
```

- note: ` control byte in a broken symlink target `

### broken_relative_spaced

- cwd: ` <root> `
- argv: ` ['<ref>', 'quoting'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f6fb6db7f2c8215ba924a1aee4a0833177fffce58a8fa2a13a0920164ff2dd96 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\' \'\n\'a b\'\n"apos\'trophe"\nback\\slash\n"both \' and ""\nbrokenrel -> \'../tar get\'\n\'broken spaced\' -> \'a b/missing\'\ncr\\rname\nctl\\u{1}name\n\'ctl target\' -> ctl\\u{1}target\ndel\\u{7f}name\ndquote"name\nesc\\u{1b}name\n\'lead space\'\nnl\\nname\nplain\ntab\\tname\n\'trail \'\n\'valid spaced\' -> \'a b\'/target\n\'with space\'\n'
```

- stderr bytes:

```python
b''
```

- note: ` brokenrel -> ../tar get; no root-level sibling; no marker `

### broken_relative_spaced_F

- cwd: ` <root> `
- argv: ` ['<ref>', '-F=always', 'quoting'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 0c37d954ea68a7885d2d4e1ac983eac926bb9becd36cd3e4de35b8ea874ac619 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\' \'\n\'a b\'/\n"apos\'trophe"\nback\\slash\n"both \' and ""\nbrokenrel -> \'../tar get\'\n\'broken spaced\' -> \'a b/missing\'\ncr\\rname\nctl\\u{1}name\n\'ctl target\' -> ctl\\u{1}target\ndel\\u{7f}name\ndquote"name\nesc\\u{1b}name\n\'lead space\'\nnl\\nname\nplain\ntab\\tname\n\'trail \'\n\'valid spaced\' -> \'a b\'/target\n\'with space\'\n'
```

- stderr bytes:

```python
b''
```

- note: ` same broken target with -F=always: still no marker `

### broken_relative_spaced_long

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', 'quoting/brokenrel'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` acd8e1c6df9f6eced5acaa1f251eebb4a06cbe373d775bfcc6353ae049a5005f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"lrwxrwxrwx - chris 13 Sep  2020 quoting/brokenrel -> '../tar get'\n"
```

- stderr bytes:

```python
b''
```

## 8. `-F`/`--classify`

### F_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-F', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### F_always_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-F=always', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2f47afd4c7c259a8222a60be7dd2b32f94dc3e19c6b67effae4e4f748731b90b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir/\ndirlink -> dir/\ngamma.txt\nlink -> alpha\nnoexec\npipe|\nprog*\nsock=\nspaced -> 'tar get'\nsub.dir/\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### Fattached_always

- cwd: ` <root> `
- argv: ` ['<ref>', '-Falways', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2f47afd4c7c259a8222a60be7dd2b32f94dc3e19c6b67effae4e4f748731b90b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir/\ndirlink -> dir/\ngamma.txt\nlink -> alpha\nnoexec\npipe|\nprog*\nsock=\nspaced -> 'tar get'\nsub.dir/\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### F_separate_always

- cwd: ` <root> `
- argv: ` ['<ref>', '-F', 'always', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2f47afd4c7c259a8222a60be7dd2b32f94dc3e19c6b67effae4e4f748731b90b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir/\ndirlink -> dir/\ngamma.txt\nlink -> alpha\nnoexec\npipe|\nprog*\nsock=\nspaced -> 'tar get'\nsub.dir/\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### classify_always

- cwd: ` <root> `
- argv: ` ['<ref>', '--classify=always', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2f47afd4c7c259a8222a60be7dd2b32f94dc3e19c6b67effae4e4f748731b90b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir/\ndirlink -> dir/\ngamma.txt\nlink -> alpha\nnoexec\npipe|\nprog*\nsock=\nspaced -> 'tar get'\nsub.dir/\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### classify_automatic

- cwd: ` <root> `
- argv: ` ['<ref>', '--classify=automatic', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### classify_separate_automatic

- cwd: ` <root> `
- argv: ` ['<ref>', '--classify', 'automatic', 'mixed'] `
- env extras: ` {} `
- exit: ` 2 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` abb8382f36f8f735b78441344f24c392fdf37ffcde83885bde2b4962ddc6a4d6 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b'"automatic": No such file or directory (os error 2)\n'
```

- note: ```automatic` is not in WHEN, so it stays an operand``

### F_separate_automatic

- cwd: ` <root> `
- argv: ` ['<ref>', '-F', 'automatic', 'mixed'] `
- env extras: ` {} `
- exit: ` 2 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` abb8382f36f8f735b78441344f24c392fdf37ffcde83885bde2b4962ddc6a4d6 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b'"automatic": No such file or directory (os error 2)\n'
```

- note: ```automatic` is not in WHEN, so it stays an operand``

### F_equals_automatic

- cwd: ` <root> `
- argv: ` ['<ref>', '-F=automatic', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### F_lines

- cwd: ` <root> `
- argv: ` ['<ref>', '-1', '-F=always', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2f47afd4c7c259a8222a60be7dd2b32f94dc3e19c6b67effae4e4f748731b90b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir/\ndirlink -> dir/\ngamma.txt\nlink -> alpha\nnoexec\npipe|\nprog*\nsock=\nspaced -> 'tar get'\nsub.dir/\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### F_links

- cwd: ` <root> `
- argv: ` ['<ref>', '-F=always', 'links'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 82be290f47dce52b95d60a0030c723516954ba4ba3db8c9efe039e939e1f0439 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"b -> real/\nchain -> realfile\ndangling -> nowhere\nexec -> exec-target*\nexec-target*\nfile\nloop1 -> loop2\nloop2 -> loop1\nreal -> realdir/\nrealdir/\nrealdirlink -> realdir/\nrealfile -> file\nspaced -> 'tar get'\n'tar get'\nto_realdirlink -> realdirlink/\n"
```

- stderr bytes:

```python
b''
```

### F_long_links

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '-F=always', 'links'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` dd38e87a9297c5fcbe7492d9b8391cdd845ff1ab2c1db40673d86b94dec870c7 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"lrwxrwxrwx  - chris 13 Sep  2020 b -> real/\nlrwxrwxrwx  - chris 13 Sep  2020 chain -> realfile\nlrwxrwxrwx  - chris 13 Sep  2020 dangling -> nowhere\nlrwxrwxrwx  - chris 13 Sep  2020 exec -> exec-target*\n.rwxr-xr-x 10 chris 13 Sep  2020 exec-target*\n.rw-r--r--  2 chris 13 Sep  2020 file\nlrwxrwxrwx  - chris 13 Sep  2020 loop1 -> loop2\nlrwxrwxrwx  - chris 13 Sep  2020 loop2 -> loop1\nlrwxrwxrwx  - chris 13 Sep  2020 real -> realdir/\ndrwxr-xr-x  - chris 13 Sep  2020 realdir/\nlrwxrwxrwx  - chris 13 Sep  2020 realdirlink -> realdir/\nlrwxrwxrwx  - chris 13 Sep  2020 realfile -> file\nlrwxrwxrwx  - chris 13 Sep  2020 spaced -> 'tar get'\n.rw-r--r--  7 chris 13 Sep  2020 'tar get'\nlrwxrwxrwx  - chris 13 Sep  2020 to_realdirlink -> realdirlink/\n"
```

- stderr bytes:

```python
b''
```

- note: ` long target marker `

### F_d_file

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-d', '-F=always', 'f'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 092fcfbbcfca3b5be7ae1b5e58538e92c35ab273ae13664fed0d67484c8e78a6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'f\n'
```

- stderr bytes:

```python
b''
```

### F_d_dir

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-d', '-F=always', 'dir'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 7c15f4516712f817e08b95a1f33569b4d639e45381a819367b584a4d715099d8 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'dir/\n'
```

- stderr bytes:

```python
b''
```

### F_d_symlink

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-d', '-F=always', 'l'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 556dd11e5fda56d99f0f0a2b40e79caf7d7af884d08165c7fce7fe4b5a5b2a5d `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'l -> f\n'
```

- stderr bytes:

```python
b''
```

### F_d_dirlink

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-d', '-F=always', 'dl'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 8a12279eeef8610a8affc32fcd15f6519ba7692288116ef0850ab7ddcd706578 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'dl -> dir/\n'
```

- stderr bytes:

```python
b''
```

### F_absolute

- cwd: ` <root> `
- argv: ` ['<ref>', '-F=always', 'absolute'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` d75556f237c370f5f01b29a81f810ba7601eaa6134c999a62288225a17fb9da9 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"root -> //\nspaceabs -> '/tar get'\ntmp -> /tmp/\ntmp_slash -> /tmp/\n"
```

- stderr bytes:

```python
b''
```

- note: ` live-only root-parent target markers `

### F_grid_width

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '-F=always', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 6c269d1557ed21b530060a07c15c58bb4edf93cb0aafa0b6f6660a715c932cf2 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-    alpha  broken@  dirlink@   link@   pipe|  sock=    sub.dir/\n--x  Beta   dir/     gamma.txt  noexec  prog*  spaced@  'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` grid marker on the link is E2; record now `

## 9. Restricted long format

### long_empty

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'empty'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b''
```

### long_one

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'one'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 9e5145ee35bfc093eed29b784d1c64cb9bdc15eeb1a7a813ecfbcff5cd17e940 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 2020-09-13 12:26 f\n'
```

- stderr bytes:

```python
b''
```

### long_onedir

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'onedir'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` b65117e14588839ece327a3895f322f69a08cf1d92101824717c1c47903ad1ee `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'drwxr-xr-x - 2020-09-13 12:26 sub\n'
```

- stderr bytes:

```python
b''
```

### long_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` db38f991229fe1ca74b5378b31027c0ac2be76c3e2a1dbd5da9f90fc0b1cfc40 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b".rw-r--r--  5 2020-09-13 12:26 -\n.rw-r--r--  9 2020-09-13 12:26 --x\n.rw-r--r--  6 2020-09-13 12:26 alpha\n.rw-r--r--  5 2020-09-13 12:26 Beta\nlrwxrwxrwx  - 2020-09-13 12:26 broken -> missing\ndrwxr-xr-x  - 2020-09-13 12:26 dir\nlrwxrwxrwx  - 2020-09-13 12:26 dirlink -> dir\n.rw-r--r--  6 2020-09-13 12:26 gamma.txt\nlrwxrwxrwx  - 2020-09-13 12:26 link -> alpha\n.rw-r--r--  3 2020-09-13 12:26 noexec\n|rw-r--r--  - 2020-09-13 12:26 pipe\n.rwxr-xr-x 17 2020-09-13 12:26 prog\nsrw-r--r--  - 2020-09-13 12:26 sock\nlrwxrwxrwx  - 2020-09-13 12:26 spaced -> 'tar get'\ndrwxr-xr-x  - 2020-09-13 12:26 sub.dir\n.rw-r--r--  7 2020-09-13 12:26 'tar get'\n"
```

- stderr bytes:

```python
b''
```

### long_modes

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'modes'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 88e5d93ed9d7d206a2ba8ffa8481af6e183351aabe69f6a278deaae6abdaf04c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'd--------- - 2020-09-13 12:26 d000\ndrwx------ - 2020-09-13 12:26 d700\ndrwxr-xr-x - 2020-09-13 12:26 d755\ndrwxrwxrwt - 2020-09-13 12:26 d1777\n.--------- 2 2020-09-13 12:26 m000\n.rw------- 2 2020-09-13 12:26 m600\n.rw-r--r-- 2 2020-09-13 12:26 m644\n.rwx------ 2 2020-09-13 12:26 m700\n.rwxr-xr-x 2 2020-09-13 12:26 m755\n.rwxrwxrwt 2 2020-09-13 12:26 m1777\n.rwxr-sr-x 2 2020-09-13 12:26 m2755\n.rwsr-xr-x 2 2020-09-13 12:26 m4755\n|rw-r--r-- - 2020-09-13 12:26 pipe\nsrw-r--r-- - 2020-09-13 12:26 sock\n'
```

- stderr bytes:

```python
b''
```

### long_sizes

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'sizes'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` cb0d0b70305218de968ba2a85dbe3fece7192b8cc86cc24d79815cd428259a1a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r--       0 2020-09-13 12:26 s000\n.rw-r--r--       1 2020-09-13 12:26 s001\n.rw-r--r-- 1048576 2020-09-13 12:26 s1m\n.rw-r--r--     999 2020-09-13 12:26 s999\n.rw-r--r--    1000 2020-09-13 12:26 s1000\n.rw-r--r--    1024 2020-09-13 12:26 s1024\n.rw-r--r--    1025 2020-09-13 12:26 s1025\n.rw-r--r--    4095 2020-09-13 12:26 s4095\n.rw-r--r--    4096 2020-09-13 12:26 s4096\n.rw-r--r-- 8388608 2020-09-13 12:26 sparse\n'
```

- stderr bytes:

```python
b''
```

### long_times

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'times'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 4aa1d9e8d6c1381e665f5c1aa64e37bb26be869001f02f44d0f2986c9cace651 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 1970-01-01 00:00 t1970\n.rw-r--r-- 2 1999-12-31 00:00 t1999\n.rw-r--r-- 2 2000-02-29 00:00 t2000\n.rw-r--r-- 2 2020-01-02 03:04 t2020\n.rw-r--r-- 2 2038-01-19 03:14 t2038\n.rw-r--r-- 2 2100-03-01 00:00 t2100\n.rw-r--r-- 2 -                tpre\n'
```

- stderr bytes:

```python
b''
```

### long_long

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'long'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 519b3fb8d1f505024ea6861e727250fa069e784e946478ed0269e6b466bf9631 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 1234567 2020-09-13 12:26 big\nlrwxrwxrwx       - 2020-09-13 12:26 dangling -> missing\ndrwxr-xr-x       - 2020-09-13 12:26 deep\ndrwxr-xr-x       - 2020-09-13 12:26 dir\n.rw-r--r--       0 2020-09-13 12:26 empty\n.rwxr-xr-x      10 2020-09-13 12:26 exec\n.rw-r--r--    1000 2020-09-13 12:26 kilo\nlrwxrwxrwx       - 2020-09-13 12:26 link -> one\n.rw-r--r--       1 2020-09-13 12:26 one\n|rw-r--r--       - 2020-09-13 12:26 pipe\n.rw-r--r--       2 -                pre\n.rw-r--r--     999 2020-09-13 12:26 small\nsrw-r--r--       - 2020-09-13 12:26 sock\n'
```

- stderr bytes:

```python
b''
```

### long_links

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'links'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 51019633aca9395f5a940eadffc716e9e19cf6259b258aa4414dddd578355fcf `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"lrwxrwxrwx  - 2020-09-13 12:26 b -> real\nlrwxrwxrwx  - 2020-09-13 12:26 chain -> realfile\nlrwxrwxrwx  - 2020-09-13 12:26 dangling -> nowhere\nlrwxrwxrwx  - 2020-09-13 12:26 exec -> exec-target\n.rwxr-xr-x 10 2020-09-13 12:26 exec-target\n.rw-r--r--  2 2020-09-13 12:26 file\nlrwxrwxrwx  - 2020-09-13 12:26 loop1 -> loop2\nlrwxrwxrwx  - 2020-09-13 12:26 loop2 -> loop1\nlrwxrwxrwx  - 2020-09-13 12:26 real -> realdir\ndrwxr-xr-x  - 2020-09-13 12:26 realdir\nlrwxrwxrwx  - 2020-09-13 12:26 realdirlink -> realdir\nlrwxrwxrwx  - 2020-09-13 12:26 realfile -> file\nlrwxrwxrwx  - 2020-09-13 12:26 spaced -> 'tar get'\n.rw-r--r--  7 2020-09-13 12:26 'tar get'\nlrwxrwxrwx  - 2020-09-13 12:26 to_realdirlink -> realdirlink\n"
```

- stderr bytes:

```python
b''
```

### long_tree

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` cc2e6a24addb1c9f99f5379d5e37a079cd1be7b87c3cdc734298e5dabf4a0cf5 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 2020-09-13 12:26 a.txt\nlrwxrwxrwx - 2020-09-13 12:26 broken -> missing\ndrwxr-xr-x - 2020-09-13 12:26 d1\nlrwxrwxrwx - 2020-09-13 12:26 dirlink -> d1\ndrwxr-xr-x - 2020-09-13 12:26 emptydir\ndrwxr-xr-x - 2020-09-13 12:26 hidden\n'
```

- stderr bytes:

```python
b''
```

### big_7digit

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'long/big'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 0ae160be7e5fba79b0051a6599558f151f07d9eba0941f857a6f2e1531298240 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 1234567 2020-09-13 12:26 long/big\n'
```

- stderr bytes:

```python
b''
```

- note: ` seven-digit --bytes size under LC_ALL=C `

### pre_epoch

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'times/tpre'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` afe504d052df256a845689f9a1d53696973fbcbe069494393906e41d150888ae `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 - times/tpre\n'
```

- stderr bytes:

```python
b''
```

- note: ``pre-1970 mtime renders `-```

### t2038

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'times/t2038'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2148e327a4ed25aaef00a4068cbfc0c738e873e6fc63404a5cdf5684776f53ae `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 2038-01-19 03:14 times/t2038\n'
```

- stderr bytes:

```python
b''
```

### setuid

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'modes/m4755'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 78f65755e06ba2af5b4afe33ae0f21843569a4bc7e5ed736fb537e6035fc8c6c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rwsr-xr-x 2 2020-09-13 12:26 modes/m4755\n'
```

- stderr bytes:

```python
b''
```

### setgid

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'modes/m2755'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` dded3cefdd194c7cc38f19712e6cf5b10a6730281aa05c4023c246adce749a0f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rwxr-sr-x 2 2020-09-13 12:26 modes/m2755\n'
```

- stderr bytes:

```python
b''
```

### sticky

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'modes/m1777'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2913edcc24a96af651f76717c36713c70186fd1cd0d29d7044dbd25795184525 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rwxrwxrwt 2 2020-09-13 12:26 modes/m1777\n'
```

- stderr bytes:

```python
b''
```

### mode000

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'modes/m000'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f2d60ef7b349384142135c72f157d0a3e3ccc40d48c5f298db5179fd2721de99 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.--------- 2 2020-09-13 12:26 modes/m000\n'
```

- stderr bytes:

```python
b''
```

### dir_mode000

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'modes/d000'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 8f4e74f89560a13a0d71f708275452b968fde440d0e6a7d956a56a4d4066996f `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'Permission denied: modes/d000 - code: 13\n\nSkipped 1 directories due to permission denied: \n  modes/d000\n'
```

### long_xattr

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'xattr'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f9fcff253861999c7b0e6d1baa4d51c0714b9c8d6e7b48f7b4e65cc938855bac `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r--@ 2 2020-09-13 12:26 file\n'
```

- stderr bytes:

```python
b''
```

- note: ` xattr fixture: the reference appends @ to the permission cell; reference data only, excluded from goldens (9.2/9.4) `

### numeric_user

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '-n', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 0a2d0cea9e18314d8d5c9a563c5c925c7352f06d260ea0a84d413f201adf8da7 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b".rw-r--r--  5 1000 13 Sep  2020 -\n.rw-r--r--  9 1000 13 Sep  2020 --x\n.rw-r--r--  6 1000 13 Sep  2020 alpha\n.rw-r--r--  5 1000 13 Sep  2020 Beta\nlrwxrwxrwx  - 1000 13 Sep  2020 broken -> missing\ndrwxr-xr-x  - 1000 13 Sep  2020 dir\nlrwxrwxrwx  - 1000 13 Sep  2020 dirlink -> dir\n.rw-r--r--  6 1000 13 Sep  2020 gamma.txt\nlrwxrwxrwx  - 1000 13 Sep  2020 link -> alpha\n.rw-r--r--  3 1000 13 Sep  2020 noexec\n|rw-r--r--  - 1000 13 Sep  2020 pipe\n.rwxr-xr-x 17 1000 13 Sep  2020 prog\nsrw-r--r--  - 1000 13 Sep  2020 sock\nlrwxrwxrwx  - 1000 13 Sep  2020 spaced -> 'tar get'\ndrwxr-xr-x  - 1000 13 Sep  2020 sub.dir\n.rw-r--r--  7 1000 13 Sep  2020 'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` numeric uid; hosts differ, live/shape-only `

### no_user

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 954902a6d38febfc1101a1f45d84cdf49aa568353ead5b8d4f879570641dcbfb `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b".rw-r--r--  5 13 Sep  2020 -\n.rw-r--r--  9 13 Sep  2020 --x\n.rw-r--r--  6 13 Sep  2020 alpha\n.rw-r--r--  5 13 Sep  2020 Beta\nlrwxrwxrwx  - 13 Sep  2020 broken -> missing\ndrwxr-xr-x  - 13 Sep  2020 dir\nlrwxrwxrwx  - 13 Sep  2020 dirlink -> dir\n.rw-r--r--  6 13 Sep  2020 gamma.txt\nlrwxrwxrwx  - 13 Sep  2020 link -> alpha\n.rw-r--r--  3 13 Sep  2020 noexec\n|rw-r--r--  - 13 Sep  2020 pipe\n.rwxr-xr-x 17 13 Sep  2020 prog\nsrw-r--r--  - 13 Sep  2020 sock\nlrwxrwxrwx  - 13 Sep  2020 spaced -> 'tar get'\ndrwxr-xr-x  - 13 Sep  2020 sub.dir\n.rw-r--r--  7 13 Sep  2020 'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` non-bytes sizes and default time style `

### default_columns

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 7551be612721ab205fbd9ffb62efcde3fb53fab2246ca4664f916683c8763bb9 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b".rw-r--r--  5 chris 13 Sep  2020 -\n.rw-r--r--  9 chris 13 Sep  2020 --x\n.rw-r--r--  6 chris 13 Sep  2020 alpha\n.rw-r--r--  5 chris 13 Sep  2020 Beta\nlrwxrwxrwx  - chris 13 Sep  2020 broken -> missing\ndrwxr-xr-x  - chris 13 Sep  2020 dir\nlrwxrwxrwx  - chris 13 Sep  2020 dirlink -> dir\n.rw-r--r--  6 chris 13 Sep  2020 gamma.txt\nlrwxrwxrwx  - chris 13 Sep  2020 link -> alpha\n.rw-r--r--  3 chris 13 Sep  2020 noexec\n|rw-r--r--  - chris 13 Sep  2020 pipe\n.rwxr-xr-x 17 chris 13 Sep  2020 prog\nsrw-r--r--  - chris 13 Sep  2020 sock\nlrwxrwxrwx  - chris 13 Sep  2020 spaced -> 'tar get'\ndrwxr-xr-x  - chris 13 Sep  2020 sub.dir\n.rw-r--r--  7 chris 13 Sep  2020 'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` user name, bytes default; host-dependent user `

### d_file

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', '-d', 'operands/f'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` ed4961ac1e6e8b87a5d67ec26b473480615483de2874bcb0d68485048f60dc89 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 2020-09-13 12:26 operands/f\n'
```

- stderr bytes:

```python
b''
```

### d_dir

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', '-d', 'operands/dir'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` a672418fbebafdb28e32e9099cd2521e3d836a8639348b14b804dc0c1168b4db `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'drwxr-xr-x - 2020-09-13 12:26 operands/dir\n'
```

- stderr bytes:

```python
b''
```

### d_symlink

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', '-d', 'operands/l'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 678d87887973e3c90863842c15cd06f41330e3efd95dbcdb55295e59d796c6fb `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'lrwxrwxrwx - 2020-09-13 12:26 operands/l -> f\n'
```

- stderr bytes:

```python
b''
```

### d_absolute

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', '-d', 'operands/x y/sub/link'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 678664ad9e59700e56e0f629b3efaaf1d447afec79692c51bc7e32690b4b9038 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"lrwxrwxrwx - 2020-09-13 12:26 'operands/x y/sub'/link -> target\n"
```

- stderr bytes:

```python
b''
```

### aa_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-aa', '-l', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2b40a7ea3faa66e431be8d5165b07833aa96d4d9da2fa79ec0c53f5155f58b6b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b".rw-r--r--  5 chris 13 Sep  2020 -\n.rw-r--r--  9 chris 13 Sep  2020 --x\ndrwxr-xr-x  - chris 13 Sep  2020 .\ndrwxr-xr-x  - chris 13 Sep  2020 ..\n.rw-r--r--  7 chris 13 Sep  2020 ..double\n.rw-r--r--  7 chris 13 Sep  2020 .hidden\n.rw-r--r--  6 chris 13 Sep  2020 alpha\n.rw-r--r--  5 chris 13 Sep  2020 Beta\nlrwxrwxrwx  - chris 13 Sep  2020 broken -> missing\ndrwxr-xr-x  - chris 13 Sep  2020 dir\nlrwxrwxrwx  - chris 13 Sep  2020 dirlink -> dir\n.rw-r--r--  6 chris 13 Sep  2020 gamma.txt\nlrwxrwxrwx  - chris 13 Sep  2020 link -> alpha\n.rw-r--r--  3 chris 13 Sep  2020 noexec\n|rw-r--r--  - chris 13 Sep  2020 pipe\n.rwxr-xr-x 17 chris 13 Sep  2020 prog\nsrw-r--r--  - chris 13 Sep  2020 sock\nlrwxrwxrwx  - chris 13 Sep  2020 spaced -> 'tar get'\ndrwxr-xr-x  - chris 13 Sep  2020 sub.dir\n.rw-r--r--  7 chris 13 Sep  2020 'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` live/shape-only dot rows `

### cwd_dot

- cwd: ` <root>/mixed `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` db38f991229fe1ca74b5378b31027c0ac2be76c3e2a1dbd5da9f90fc0b1cfc40 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b".rw-r--r--  5 2020-09-13 12:26 -\n.rw-r--r--  9 2020-09-13 12:26 --x\n.rw-r--r--  6 2020-09-13 12:26 alpha\n.rw-r--r--  5 2020-09-13 12:26 Beta\nlrwxrwxrwx  - 2020-09-13 12:26 broken -> missing\ndrwxr-xr-x  - 2020-09-13 12:26 dir\nlrwxrwxrwx  - 2020-09-13 12:26 dirlink -> dir\n.rw-r--r--  6 2020-09-13 12:26 gamma.txt\nlrwxrwxrwx  - 2020-09-13 12:26 link -> alpha\n.rw-r--r--  3 2020-09-13 12:26 noexec\n|rw-r--r--  - 2020-09-13 12:26 pipe\n.rwxr-xr-x 17 2020-09-13 12:26 prog\nsrw-r--r--  - 2020-09-13 12:26 sock\nlrwxrwxrwx  - 2020-09-13 12:26 spaced -> 'tar get'\ndrwxr-xr-x  - 2020-09-13 12:26 sub.dir\n.rw-r--r--  7 2020-09-13 12:26 'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` pinned directory metadata; parent does not appear with -d? no, listing `

### cwd_dot_d

- cwd: ` <root>/mixed `
- argv: ` ['<ref>', '-l', '-d', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` d6b4c724456588c3c664355a34cdf3b6761230ae2c44112c09b3e31780094978 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'drwxr-xr-x - chris 13 Sep  2020 .\n'
```

- stderr bytes:

```python
b''
```

- note: ` pinned directory metadata `

### dotdot_d

- cwd: ` <root>/one `
- argv: ` ['<ref>', '-l', '-d', '..'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` d9a33fce28b426ace91bb1227c7275a2f2fe9b2d778f0fab694399235b1bd538 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'drwxr-xr-x - chris 13 Sep  2020 ..\n'
```

- stderr bytes:

```python
b''
```

- note: ` live-only host parent metadata `

### root_d

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '-d', '/'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` d7b8e7eac081bb94ce2bbf626dc2304864ebfb8c2aa9d857d6fe871dd808cafe `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'drwxr-xr-x - root 25 Oct 09:05 /\n'
```

- stderr bytes:

```python
b''
```

- note: ` live-only host root metadata `

### names_hidden

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', '-a', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f53a080a38b17e47446fe879fa9cf29443a373bed948fd10bee1b474768639f2 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b".rw-r--r-- 2 2020-09-13 12:26 -\n.rw-r--r-- 2 2020-09-13 12:26 --\n.rw-r--r-- 2 2020-09-13 12:26 --x\n.rw-r--r-- 2 2020-09-13 12:26 -x\n.rw-r--r-- 2 2020-09-13 12:26 ' .'\n.rw-r--r-- 2 2020-09-13 12:26 .hidden\n.rw-r--r-- 2 2020-09-13 12:26 001\n.rw-r--r-- 2 2020-09-13 12:26 01\n.rw-r--r-- 2 2020-09-13 12:26 1\n.rw-r--r-- 2 2020-09-13 12:26 2\n.rw-r--r-- 2 2020-09-13 12:26 10\n.rw-r--r-- 2 2020-09-13 12:26 a\n.rw-r--r-- 2 2020-09-13 12:26 A\n.rw-r--r-- 2 2020-09-13 12:26 ' a'\n.rw-r--r-- 2 2020-09-13 12:26 'a b'\n.rw-r--r-- 2 2020-09-13 12:26 B\n.rw-r--r-- 2 2020-09-13 12:26 b\n.rw-r--r-- 2 2020-09-13 12:26 dot.\ndrwxr-xr-x - 2020-09-13 12:26 ordered\n.rw-r--r-- 2 2020-09-13 12:26 'pic 4'\n.rw-r--r-- 2 2020-09-13 12:26 pic4\n.rw-r--r-- 2 2020-09-13 12:26 qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\n.rw-r--r-- 2 2020-09-13 12:26 readme\n.rw-r--r-- 2 2020-09-13 12:26 README\n.rw-r--r-- 2 2020-09-13 12:26 rfc1\n.rw-r--r-- 2 2020-09-13 12:26 rfc2\n.rw-r--r-- 2 2020-09-13 12:26 rfc10\ndrwxr-xr-x - 2020-09-13 12:26 tie_Aa\ndrwxr-xr-x - 2020-09-13 12:26 tie_dot\ndrwxr-xr-x - 2020-09-13 12:26 tie_pic\ndrwxr-xr-x - 2020-09-13 12:26 tie_readme\ndrwxr-xr-x - 2020-09-13 12:26 tie_space\n.rw-r--r-- 2 2020-09-13 12:26 under_score\n.rw-r--r-- 2 2020-09-13 12:26 UPPER\n.rw-r--r-- 2 2020-09-13 12:26 x-\n.rw-r--r-- 2 2020-09-13 12:26 Z\n.rw-r--r-- 2 2020-09-13 12:26 z\n.rw-r--r-- 2 2020-09-13 12:26 ~\n"
```

- stderr bytes:

```python
b''
```

## 10. Errors, exit statuses and write failures

### missing_operand

- cwd: ` <root> `
- argv: ` ['<ref>', 'missing'] `
- env extras: ` {} `
- exit: ` 2 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 0fb9a28754aa4ff311917598c48cd52a9f34351d8b7f72616ef6d67a3f0d4bfb `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'"missing": No such file or directory (os error 2)\n'
```

### file_as_directory

- cwd: ` <root> `
- argv: ` ['<ref>', 'one/f/'] `
- env extras: ` {} `
- exit: ` 2 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 7ad317ba1d9f5b34da893707ed96156e94117b4749d6caf735f5d6f4df77d782 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'"one/f/": Not a directory (os error 20)\n'
```

### permission_denied

- cwd: ` <root> `
- argv: ` ['<ref>', 'locked/denied'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` d7ae729a82ec6beccfd4f0cd8ca71072fa9130ee19f8dd032ea8f025cf20dc5e `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'Permission denied: locked/denied - code: 13\n\nSkipped 1 directories due to permission denied: \n  locked/denied\n'
```

- note: ` skipped when euid is 0 `

### permission_denied_entry

- cwd: ` <root> `
- argv: ` ['<ref>', 'locked/denied/f'] `
- env extras: ` {} `
- exit: ` 2 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` cbffadd65422dac712edf9bf9f261dd346261e128f4623482f6e280f867ff366 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'"locked/denied/f": Permission denied (os error 13)\n'
```

- note: ` metadata failure under a 000 directory `

### unknown_short

- cwd: ` <root> `
- argv: ` ['<ref>', '-Q', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` c7fbe8b3ac2dbb6b3d99d9fc3df52bddc5bcde694c2ea5d0a6d899effa3ff7a4 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Unknown argument -Q\n'
```

### unknown_short_attached

- cwd: ` <root> `
- argv: ` ['<ref>', '-Qfoo', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` c7fbe8b3ac2dbb6b3d99d9fc3df52bddc5bcde694c2ea5d0a6d899effa3ff7a4 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Unknown argument -Q\n'
```

### unknown_short_equals

- cwd: ` <root> `
- argv: ` ['<ref>', '-Q=foo', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` c7fbe8b3ac2dbb6b3d99d9fc3df52bddc5bcde694c2ea5d0a6d899effa3ff7a4 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Unknown argument -Q\n'
```

### unknown_long

- cwd: ` <root> `
- argv: ` ['<ref>', '--foo', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 3939a49fda38b40066056364560f452b203d305438e3e79753fa746c156b299b `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Unknown argument --foo\n'
```

### unknown_long_eq

- cwd: ` <root> `
- argv: ` ['<ref>', '--foo=bar', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 3939a49fda38b40066056364560f452b203d305438e3e79753fa746c156b299b `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Unknown argument --foo\n'
```

### unknown_long_double_eq

- cwd: ` <root> `
- argv: ` ['<ref>', '--foo==', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 3939a49fda38b40066056364560f452b203d305438e3e79753fa746c156b299b `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Unknown argument --foo\n'
```

### unknown_long_empty_value

- cwd: ` <root> `
- argv: ` ['<ref>', '--foo=', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 700b4254488c8fdb589d6121800e9b7869f00387cd37b013b92183119586ac43 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Unknown argument --foo=\n'
```

### unknown_long_weird

- cwd: ` <root> `
- argv: ` ['<ref>', '--=x', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 64713661f83ee1cb72f4f7bb6dcb1175301d0de29bdfe2000523feb8357b177b `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Unknown argument --=x\n'
```

### missing_value_sort

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` ef8f44cfd0f21126fca2fba2790f06a42295fb5f48b0d8bb79b9d8d9631fba8b `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Flag --sort needs a value (choices: name, Name, size, extension, Extension, modified, changed, accessed, created, inode, type, none)\n'
```

### missing_value_width

- cwd: ` <root> `
- argv: ` ['<ref>', '--width'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e521fe79fffb8422ad47d469856757efcb5a820e29f0b4b3be7948ff8d81242c `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Flag --width needs a value\n'
```

### missing_value_level

- cwd: ` <root> `
- argv: ` ['<ref>', '-L'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` c49241cc0b9a503c7e91b3f8786485c5dfddfc54cdcfc49a34c67986b9515f99 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Flag -L needs a value\n'
```

### empty_value_sort

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` d41ac89b115f0bdd750402b1a3989bc83aa7cc68aa45574cb5c1638a5b85681e `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Unknown argument --sort=\n'
```

### empty_value_color

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 8029107e55164d45102cd27c10e641ff22aafa40edb42f2b9889ac905984a78b `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Unknown argument --color=\n'
```

### empty_value_icons

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` c477195242468977c2256f41795ead194421112396b33e492d2243955f9671f5 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Unknown argument --icons=\n'
```

### bad_value_sort

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=bogus', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` de467b8824da9bf15f311364899372dcfba78259c73d2531d9a3ea615f91c289 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Option --sort (-s) has no "bogus" setting (choices: name, Name, size, extension, Extension, modified, changed, accessed, created, inode, type, none)\n'
```

### bad_value_classify

- cwd: ` <root> `
- argv: ` ['<ref>', '--classify=bogus', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 21222db920aaa654ad53daf3244b52005694f07da4d254b53897412fb8e5dac7 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Option --classify (-F) has no "bogus" setting\n'
```

### bad_value_color

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=bogus', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 718095960cc80e4d2a8660563058e46dba4155b1443db3148f2b4a31d21ef1a9 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Option --color has no "bogus" setting\n'
```

### bad_value_icons

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=bogus', 'mixed'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 3052e8151aa2cb9157c6e91f363ab7a04510eee7db9ccec6826e7ba8f0f2dc4e `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Option --icons has no "bogus" setting\n'
```

### bad_value_time_style

- cwd: ` <root> `
- argv: ` ['<ref>', '--time-style=bogus', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### bad_value_level

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-L', 'x', 'tree'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 5994f139f76fcaf9454ea38c0f3ddffab14f6ee6e23f9e009a3112187239ae0c `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "x" not valid for option --level (-L): invalid digit found in string\n'
```

### dev_full

- cwd: ` <root> `
- argv: ` ['<ref>', 'mixed'] `
- env extras: ` {} `
- exit: ` 1 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 09784d5ffbdb95211341319e0e74ba00699a9c07142bcb34fd57a43d24ffc266 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'No space left on device (os error 28)\n'
```

- note: ` stdout is /dev/full; stdout is /dev/full; captured bytes are empty `

### broken_pipe

- cwd: ` <root> `
- argv: ` ['<ref>', 'many'] `
- env extras: ` {} `
- exit: ` signal 13 (SIGPIPE) `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b''
```

- note: ` stdout read end closed before the first write; stdout pipe read end closed before the first write `

### path_over_limit

- cwd: ` <root> `
- argv: ` ['<ref>', 'a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/f'] `
- env extras: ` {} `
- exit: ` 2 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` ea759ac2df86660812f44b241ed72a78390d67dc1d4d7dfcf0aa8cb9a08e02e3 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'"a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/f": File name too long (os error 36)\n'
```

- note: ` over-limit operand: expected error/limitation, not a golden `

### path_near_limit

- cwd: ` <root> `
- argv: ` ['<ref>', 'long/deep/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/f'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 248c6d58a2fce17488edb8f50c0ba28bb4f7d4504c432f63df8185229be7dde7 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'long/deep/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/dzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/f\n'
```

- stderr bytes:

```python
b''
```

- note: ` joined path under 4095 bytes `

## 11. Symlink operands and root-parent targets

### operand_file_link

- cwd: ` <root> `
- argv: ` ['<ref>', 'links/realfile'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 6946cb732a79d2c9970693b53e7d6c9b848a6cbeb99666d0790ae6b1e41e9c85 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'links/realfile -> file\n'
```

- stderr bytes:

```python
b''
```

### operand_dir_link

- cwd: ` <root> `
- argv: ` ['<ref>', 'links/realdirlink'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b''
```

### operand_dir_link_slash

- cwd: ` <root> `
- argv: ` ['<ref>', 'links/realdirlink/'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b''
```

### operand_dangling

- cwd: ` <root> `
- argv: ` ['<ref>', 'links/dangling'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` b2a30a4ecc03fb2e711f46114e3ea1b59585a30a6d2953bde1acdd5b5122d149 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'links/dangling -> nowhere\n'
```

- stderr bytes:

```python
b''
```

### operand_dangling_slash

- cwd: ` <root> `
- argv: ` ['<ref>', 'links/dangling/'] `
- env extras: ` {} `
- exit: ` 2 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 95e85af858c97d0ae6fb4e2497d7deaae9f5811b738509899761530eed5ac072 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'"links/dangling/": No such file or directory (os error 2)\n'
```

### operand_chain

- cwd: ` <root> `
- argv: ` ['<ref>', 'links/chain'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` b883ea57c87f1dae87ccc9476e2f3e02f0fca4c2b8ce1c75c6b016d0efeadd0a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'links/chain -> realfile\n'
```

- stderr bytes:

```python
b''
```

### operand_loop

- cwd: ` <root> `
- argv: ` ['<ref>', 'links/loop1'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 902c2e3f7839b59760ea43d2ba81c4cf355bbe7e82ee3b188fcba1b2aa5dc284 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'links/loop1 -> loop2\n'
```

- stderr bytes:

```python
b''
```

### operand_b

- cwd: ` <root> `
- argv: ` ['<ref>', 'links/b'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b''
```

### cwd_link_file

- cwd: ` <root>/links `
- argv: ` ['<ref>', 'realfile'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 56108b541c33099caf8dffbe4f7c8e64494976139e23c574691eb6f8806c0b66 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'realfile -> file\n'
```

- stderr bytes:

```python
b''
```

### cwd_link_dir

- cwd: ` <root>/links `
- argv: ` ['<ref>', 'realdirlink'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b''
```

### cwd_dangling

- cwd: ` <root>/links `
- argv: ` ['<ref>', 'dangling'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` dc24e4732bed269e95eee6201f93329dfa64405744b453eae522fee3f0656c6f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'dangling -> nowhere\n'
```

- stderr bytes:

```python
b''
```

### d_dirlink

- cwd: ` <root> `
- argv: ` ['<ref>', '-d', 'links/realdirlink'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 8d3a3b6fab99f4fa0247462dde0cab39f7ae19499c3533c8f2ba54ee112ee9cb `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'links/realdirlink -> realdir\n'
```

- stderr bytes:

```python
b''
```

### d_filelink

- cwd: ` <root> `
- argv: ` ['<ref>', '-d', 'links/realfile'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 6946cb732a79d2c9970693b53e7d6c9b848a6cbeb99666d0790ae6b1e41e9c85 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'links/realfile -> file\n'
```

- stderr bytes:

```python
b''
```

### long_dangling

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', 'links/dangling'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 6c5359c1434c7a0f2df69c2937b462949590c2d37dcf7a00dea3383d535d57df `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'lrwxrwxrwx - chris 13 Sep  2020 links/dangling -> nowhere\n'
```

- stderr bytes:

```python
b''
```

### long_dirlink

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', 'links/realdirlink'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b''
```

### long_loop

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', 'links/loop1'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 8e81882bcf3d55c965261ab7c4db41e54d4d759b7e2f32b25ed0101e5a5a04e6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'lrwxrwxrwx - chris 13 Sep  2020 links/loop1 -> loop2\n'
```

- stderr bytes:

```python
b''
```

### absolute_tmp

- cwd: ` <root> `
- argv: ` ['<ref>', '-d', 'absolute/tmp'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 4b40b8046009fc7eea83cae37b176729da88dc0735231dabdc4643fd58a8005d `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'absolute/tmp -> /tmp\n'
```

- stderr bytes:

```python
b''
```

- note: ` -d: absolute target display /tmp, no host listing `

### absolute_tmp_slash

- cwd: ` <root> `
- argv: ` ['<ref>', '-d', 'absolute/tmp_slash'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` db46ac38f6e772c7d77f577ac29a2702d7a5316debe91a76c75fb2e1260d58dd `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'absolute/tmp_slash -> /tmp\n'
```

- stderr bytes:

```python
b''
```

- note: ` -d: raw target /tmp/ is normalized to /tmp in the display `

### absolute_tmp_F

- cwd: ` <root> `
- argv: ` ['<ref>', '-d', '-F=always', 'absolute/tmp'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 6a64eb7b9459494600e2f9435ca5404a7a00f82f056bb4294f62b899fa515551 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'absolute/tmp -> /tmp/\n'
```

- stderr bytes:

```python
b''
```

- note: ` -d with -F=always: /tmp plus the directory marker `

### absolute_tmp_slash_F

- cwd: ` <root> `
- argv: ` ['<ref>', '-d', '-F=always', 'absolute/tmp_slash'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 713264788b5c5b5cc7df46e4c526174dec5ef34e926004b15273a5d197e06af6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'absolute/tmp_slash -> /tmp/\n'
```

- stderr bytes:

```python
b''
```

- note: ` -d with -F=always: /tmp/ `

### absolute_root

- cwd: ` <root> `
- argv: ` ['<ref>', '-d', 'absolute/root'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 66b68e824fef283fd32f704d913e16a639dc77bc0360e8f65d3da43d4e2e9ed1 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'absolute/root -> /\n'
```

- stderr bytes:

```python
b''
```

- note: ` -d: absolute target display / `

### absolute_root_F

- cwd: ` <root> `
- argv: ` ['<ref>', '-d', '-F=always', 'absolute/root'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` cb27115f405361f428217dadc34816ec21277b42a06079dc7a3d9050bffb5131 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'absolute/root -> //\n'
```

- stderr bytes:

```python
b''
```

- note: ` expected // with the directory marker `

### absolute_space_broken

- cwd: ` <root> `
- argv: ` ['<ref>', 'absolute/spaceabs'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 7a6b1c87e2fc0152fdbe32ee3666771cabafe4c840bbfabfbda95dbeb35d1feb `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"absolute/spaceabs -> '/tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` expected '/tar get' with no marker `

### absolute_space_broken_F

- cwd: ` <root> `
- argv: ` ['<ref>', '-F=always', 'absolute/spaceabs'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 7a6b1c87e2fc0152fdbe32ee3666771cabafe4c840bbfabfbda95dbeb35d1feb `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"absolute/spaceabs -> '/tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` still no marker `

### broken_relative_no_sibling

- cwd: ` <root> `
- argv: ` ['<ref>', 'quoting/brokenrel'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 442e34c751083c6f60be425f25cee157a5c52301b53bc89c93a1de6becfae258 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"quoting/brokenrel -> '../tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` expected 'link -> ../tar get' with no marker `

### broken_relative_no_sibling_F

- cwd: ` <root> `
- argv: ` ['<ref>', '-F=always', 'quoting/brokenrel'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 442e34c751083c6f60be425f25cee157a5c52301b53bc89c93a1de6becfae258 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"quoting/brokenrel -> '../tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` still no marker `

### d_sub_link

- cwd: ` <root> `
- argv: ` ['<ref>', '-d', 'operands/sub/link'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 30bc22c013d6d6fa13e31429b51dcadedcbf4c5d68c6d0c21c6f9a4ac1db33ec `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'operands/sub/link -> target\n'
```

- stderr bytes:

```python
b''
```

### d_sub_dirlink

- cwd: ` <root> `
- argv: ` ['<ref>', '-d', 'operands/sub/dirlink'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 7279164c5526bafe3c80b6af0948a1b44aae322c509183c33aad77c57e1eaa2d `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'operands/sub/dirlink -> real\n'
```

- stderr bytes:

```python
b''
```

### d_sub_link_F

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-d', '-F=always', 'sub/link'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` c2632508d9c9594b1d7d65958c71b5c932a34bd905c0d1eaf55610ab944f54d2 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'sub/link -> target*\n'
```

- stderr bytes:

```python
b''
```

- note: ` cwd-vs-operand-parent: the cwd decoy target is 0644 and sub/target is 0755, so resolution uses the operand parent `

### d_sub_dirlink_F

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-d', '-F=always', 'sub/dirlink'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 1d629cac82218ed28fb3c621a694206d84832f08161c80746af31818cc210a00 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'sub/dirlink -> real/\n'
```

- stderr bytes:

```python
b''
```

- note: ` operand-parent directory target -> real/ `

### spaced_parent_link_d

- cwd: ` <root> `
- argv: ` ['<ref>', '-d', 'operands/x y/sub/link'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 27043ed0853fd5652e590995fa5fcdc4f6dc89847bbb76774934112657d7dc90 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"'operands/x y/sub'/link -> target\n"
```

- stderr bytes:

```python
b''
```

### spaced_parent_link_d_F

- cwd: ` <root>/operands `
- argv: ` ['<ref>', '-d', '-F=always', 'x y/sub/link'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e6da7ba9e76709126753a3756ed08497f55f609ba84160525b5ef477847733b5 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"'x y/sub'/link -> target*\n"
```

- stderr bytes:

```python
b''
```

- note: ` spaced operand parent: 'x y/sub'/link -> target* `

### spaced_parent_link_long

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', 'operands/x y/sub/link'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` fd8c1cdd87dba8930e59586792992ddd4933a0e32e0340471f1fe0c4bfebb71a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"lrwxrwxrwx - chris 13 Sep  2020 'operands/x y/sub'/link -> target\n"
```

- stderr bytes:

```python
b''
```

### spaced_parent_file

- cwd: ` <root> `
- argv: ` ['<ref>', 'operands/x y/sub/f'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 719c9238871f77e8c4810e1828b6a39f96036c1265d357f143b147ef9aae944b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"'operands/x y/sub'/f\n"
```

- stderr bytes:

```python
b''
```

## 12. Colour and icons

### icons_default

- cwd: ` <root> `
- argv: ` ['<ref>', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### icons_bare

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### icons_never

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### icons_auto

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=auto', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### icons_automatic

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=automatic', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### icons_always

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=always', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` a98ab94a40aa2ef76d16101e6f21fd0a4d2c8bc5ba7610ce7edb4aa4ef17ed74 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"\xf3\xb0\xa1\xaf -\n\xf3\xb0\xa1\xaf --x\n\xf3\xb0\xa1\xaf alpha\n\xf3\xb0\xa1\xaf Beta\n\xf3\xb0\xa1\xaf broken -> missing\n\xef\x84\x95 dir\n\xee\x97\xbf dirlink -> dir\n\xef\x85\x9c gamma.txt\n\xf3\xb0\xa1\xaf link -> alpha\n\xf3\xb0\xa1\xaf noexec\n\xf3\xb0\xa1\xaf pipe\n\xf3\xb0\xa1\xaf prog\n\xf3\xb0\xa1\xaf sock\n\xf3\xb0\xa1\xaf spaced -> 'tar get'\n\xef\x84\x95 sub.dir\n\xf3\xb0\xa1\xaf 'tar get'\n"
```

- stderr bytes:

```python
b''
```

### color_bare

- cwd: ` <root> `
- argv: ` ['<ref>', '--color', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### colour_bare

- cwd: ` <root> `
- argv: ` ['<ref>', '--colour', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### color_auto

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=auto', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### color_automatic

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=automatic', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### color_never

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### color_always

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 8dffaad805181466de7add40f4743e7cf20908ff09ebad41bdf1b0efc25ccc5e `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;34mdir\x1b[0m\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\ngamma.txt\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;34msub.dir\x1b[0m\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### color_always_long

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--color=always', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 708245b2d320e0d6f0ba3a0766c389320a1e42d74878648a93cdeb04f118be3d `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b".\x1b[1;33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[32m5\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m -\n.\x1b[1;33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[32m9\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m --x\n.\x1b[1;33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[32m6\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m alpha\n.\x1b[1;33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[32m5\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m Beta\n\x1b[36ml\x1b[1;33mr\x1b[31mw\x1b[32mx\x1b[0m\x1b[33mr\x1b[31mw\x1b[32mx\x1b[33mr\x1b[31mw\x1b[32mx\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m \x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;34md\x1b[33mr\x1b[31mw\x1b[32mx\x1b[0m\x1b[33mr\x1b[1;90m-\x1b[0m\x1b[32mx\x1b[33mr\x1b[1;90m-\x1b[0m\x1b[32mx\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m \x1b[1;34mdir\x1b[0m\n\x1b[36ml\x1b[1;33mr\x1b[31mw\x1b[32mx\x1b[0m\x1b[33mr\x1b[31mw\x1b[32mx\x1b[33mr\x1b[31mw\x1b[32mx\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m \x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\n.\x1b[1;33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[32m6\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m gamma.txt\n\x1b[36ml\x1b[1;33mr\x1b[31mw\x1b[32mx\x1b[0m\x1b[33mr\x1b[31mw\x1b[32mx\x1b[33mr\x1b[31mw\x1b[32mx\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m \x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\n.\x1b[1;33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[32m3\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m noexec\n\x1b[33m|\x1b[1mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m \x1b[33mpipe\x1b[0m\n.\x1b[1;33mr\x1b[31mw\x1b[4;32mx\x1b[0m\x1b[33mr\x1b[1;90m-\x1b[0m\x1b[32mx\x1b[33mr\x1b[1;90m-\x1b[0m\x1b[32mx\x1b[0m \x1b[32m17\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m \x1b[1;32mprog\x1b[0m\n\x1b[1;31ms\x1b[33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m \x1b[1;31msock\x1b[0m\n\x1b[36ml\x1b[1;33mr\x1b[31mw\x1b[32mx\x1b[0m\x1b[33mr\x1b[31mw\x1b[32mx\x1b[33mr\x1b[31mw\x1b[32mx\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m \x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;34md\x1b[33mr\x1b[31mw\x1b[32mx\x1b[0m\x1b[33mr\x1b[1;90m-\x1b[0m\x1b[32mx\x1b[33mr\x1b[1;90m-\x1b[0m\x1b[32mx\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m \x1b[1;34msub.dir\x1b[0m\n.\x1b[1;33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[32m7\x1b[0m \x1b[1;33mchris\x1b[0m \x1b[34m13 Sep  2020\x1b[0m 'tar get'\n"
```

- stderr bytes:

```python
b''
```

### no_color_env

- cwd: ` <root> `
- argv: ` ['<ref>', 'mixed'] `
- env extras: ` {'NO_COLOR': '1'} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### no_color_with_color_always

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'NO_COLOR': '1'} `
- exit: ` 0 `
- stdout sha256: ` 8dffaad805181466de7add40f4743e7cf20908ff09ebad41bdf1b0efc25ccc5e `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;34mdir\x1b[0m\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\ngamma.txt\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;34msub.dir\x1b[0m\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### no_color_with_color_auto

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=auto', 'mixed'] `
- env extras: ` {'NO_COLOR': '1'} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### ls_colors

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'LS_COLORS': 'di=01;34:*.txt=01;35'} `
- exit: ` 0 `
- stdout sha256: ` 230f88658e8572a88f2ef3c395b14b02ef088e0f2c3a0f942afaa78d63cd3818 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;34mdir\x1b[0m\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\n\x1b[1;35mgamma.txt\x1b[0m\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;34msub.dir\x1b[0m\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### eza_colors

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'EZA_COLORS': 'di=01;32'} `
- exit: ` 0 `
- stdout sha256: ` ec4488dff1d9cbae19856a8cbd5232ad8f8e7d9d4fda69640bb7424a7887884d `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;32mdir\x1b[0m\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;32mdir\x1b[0m\ngamma.txt\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;32msub.dir\x1b[0m\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### exa_colors

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'EXA_COLORS': 'di=01;33'} `
- exit: ` 0 `
- stdout sha256: ` 37815f60f76cb45207884bf1b01a82044bb77556f300aa5b9c982e62e1abdee0 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;33mdir\x1b[0m\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;33mdir\x1b[0m\ngamma.txt\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;33msub.dir\x1b[0m\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### color_and_icons_always

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', '--icons=always', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 112bbf9349ec1fa439955d6f66621493afb02b762b0141e0c455869e5ac47052 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"\xf3\xb0\xa1\xaf -\n\xf3\xb0\xa1\xaf --x\n\xf3\xb0\xa1\xaf alpha\n\xf3\xb0\xa1\xaf Beta\n\x1b[36m\xf3\xb0\xa1\xaf broken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[34m\xef\x84\x95 \x1b[1mdir\x1b[0m\n\x1b[36m\xee\x97\xbf dirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\n\xef\x85\x9c gamma.txt\n\x1b[36m\xf3\xb0\xa1\xaf link\x1b[0m \x1b[1;90m->\x1b[0m alpha\n\xf3\xb0\xa1\xaf noexec\n\x1b[33m\xf3\xb0\xa1\xaf pipe\x1b[0m\n\x1b[32m\xf3\xb0\xa1\xaf \x1b[1mprog\x1b[0m\n\x1b[31m\xf3\xb0\xa1\xaf \x1b[1msock\x1b[0m\n\x1b[36m\xf3\xb0\xa1\xaf spaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[34m\xef\x84\x95 \x1b[1msub.dir\x1b[0m\n\xf3\xb0\xa1\xaf 'tar get'\n"
```

- stderr bytes:

```python
b''
```

### color_auto_icons_auto

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=auto', '--icons=auto', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### color_never_icons_always

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=never', '--icons=always', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` a98ab94a40aa2ef76d16101e6f21fd0a4d2c8bc5ba7610ce7edb4aa4ef17ed74 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"\xf3\xb0\xa1\xaf -\n\xf3\xb0\xa1\xaf --x\n\xf3\xb0\xa1\xaf alpha\n\xf3\xb0\xa1\xaf Beta\n\xf3\xb0\xa1\xaf broken -> missing\n\xef\x84\x95 dir\n\xee\x97\xbf dirlink -> dir\n\xef\x85\x9c gamma.txt\n\xf3\xb0\xa1\xaf link -> alpha\n\xf3\xb0\xa1\xaf noexec\n\xf3\xb0\xa1\xaf pipe\n\xf3\xb0\xa1\xaf prog\n\xf3\xb0\xa1\xaf sock\n\xf3\xb0\xa1\xaf spaced -> 'tar get'\n\xef\x84\x95 sub.dir\n\xf3\xb0\xa1\xaf 'tar get'\n"
```

- stderr bytes:

```python
b''
```

## 13. Grid, `--width`, and a pty

### width_20_names

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=20', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### width_40_names

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### width_80_names

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### width_120_names

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=120', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### width_0_names

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=0', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### width_1_names

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=1', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### width_40_grid2

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', 'grid2'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` d9fa304f62b88121a54d2b8f838fe1a9ea5d1f7ded907daa0e786d191f579754 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g0  g1\n'
```

- stderr bytes:

```python
b''
```

### width_40_grid10

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', 'grid10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 9598159f25b3d2ca0c7034ce2c4f9466090bcdc1cd679600b783fd41475ac11f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g0  g1  g2  g3  g4  g5  g6  g7  g8  g9\n'
```

- stderr bytes:

```python
b''
```

### width_80_grid100

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', 'grid100'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` ef8700ac2a4609eb848f641408abf6bc22ca6d3ccce58b22d494241e939ea779 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g00  g07  g14  g21  g28  g35  g42  g49  g56  g63  g70  g77  g84  g91  g98\ng01  g08  g15  g22  g29  g36  g43  g50  g57  g64  g71  g78  g85  g92  g99\ng02  g09  g16  g23  g30  g37  g44  g51  g58  g65  g72  g79  g86  g93\ng03  g10  g17  g24  g31  g38  g45  g52  g59  g66  g73  g80  g87  g94\ng04  g11  g18  g25  g32  g39  g46  g53  g60  g67  g74  g81  g88  g95\ng05  g12  g19  g26  g33  g40  g47  g54  g61  g68  g75  g82  g89  g96\ng06  g13  g20  g27  g34  g41  g48  g55  g62  g69  g76  g83  g90  g97\n'
```

- stderr bytes:

```python
b''
```

### width_80_gridlong10

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', 'gridlong10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` d27c44aab1c83791ac9ae6cfb561ad04edc88cbad9f0a96026fde7231f36ddf5 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'file-number-00-with-a-long-name  file-number-05-with-a-long-name\nfile-number-01-with-a-long-name  file-number-06-with-a-long-name\nfile-number-02-with-a-long-name  file-number-07-with-a-long-name\nfile-number-03-with-a-long-name  file-number-08-with-a-long-name\nfile-number-04-with-a-long-name  file-number-09-with-a-long-name\n'
```

- stderr bytes:

```python
b''
```

### width_80_gridlong100

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', 'gridlong100'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 1c4e761db27cf66eb61224dd53d95002e076cdf63c0878161ea353c4a9d6db96 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'file-number-000-with-a-long-name  file-number-050-with-a-long-name\nfile-number-001-with-a-long-name  file-number-051-with-a-long-name\nfile-number-002-with-a-long-name  file-number-052-with-a-long-name\nfile-number-003-with-a-long-name  file-number-053-with-a-long-name\nfile-number-004-with-a-long-name  file-number-054-with-a-long-name\nfile-number-005-with-a-long-name  file-number-055-with-a-long-name\nfile-number-006-with-a-long-name  file-number-056-with-a-long-name\nfile-number-007-with-a-long-name  file-number-057-with-a-long-name\nfile-number-008-with-a-long-name  file-number-058-with-a-long-name\nfile-number-009-with-a-long-name  file-number-059-with-a-long-name\nfile-number-010-with-a-long-name  file-number-060-with-a-long-name\nfile-number-011-with-a-long-name  file-number-061-with-a-long-name\nfile-number-012-with-a-long-name  file-number-062-with-a-long-name\nfile-number-013-with-a-long-name  file-number-063-with-a-long-name\nfile-number-014-with-a-long-name  file-number-064-with-a-long-name\nfile-number-015-with-a-long-name  file-number-065-with-a-long-name\nfile-number-016-with-a-long-name  file-number-066-with-a-long-name\nfile-number-017-with-a-long-name  file-number-067-with-a-long-name\nfile-number-018-with-a-long-name  file-number-068-with-a-long-name\nfile-number-019-with-a-long-name  file-number-069-with-a-long-name\nfile-number-020-with-a-long-name  file-number-070-with-a-long-name\nfile-number-021-with-a-long-name  file-number-071-with-a-long-name\nfile-number-022-with-a-long-name  file-number-072-with-a-long-name\nfile-number-023-with-a-long-name  file-number-073-with-a-long-name\nfile-number-024-with-a-long-name  file-number-074-with-a-long-name\nfile-number-025-with-a-long-name  file-number-075-with-a-long-name\nfile-number-026-with-a-long-name  file-number-076-with-a-long-name\nfile-number-027-with-a-long-name  file-number-077-with-a-long-name\nfile-number-028-with-a-long-name  file-number-078-with-a-long-name\nfile-number-029-with-a-long-name  file-number-079-with-a-long-name\nfile-number-030-with-a-long-name  file-number-080-with-a-long-name\nfile-number-031-with-a-long-name  file-number-081-with-a-long-name\nfile-number-032-with-a-long-name  file-number-082-with-a-long-name\nfile-number-033-with-a-long-name  file-number-083-with-a-long-name\nfile-number-034-with-a-long-name  file-number-084-with-a-long-name\nfile-number-035-with-a-long-name  file-number-085-with-a-long-name\nfile-number-036-with-a-long-name  file-number-086-with-a-long-name\nfile-number-037-with-a-long-name  file-number-087-with-a-long-name\nfile-number-038-with-a-long-name  file-number-088-with-a-long-name\nfile-number-039-with-a-long-name  file-number-089-with-a-long-name\nfile-number-040-with-a-long-name  file-number-090-with-a-long-name\nfile-number-041-with-a-long-name  file-number-091-with-a-long-name\nfile-number-042-with-a-long-name  file-number-092-with-a-long-name\nfile-number-043-with-a-long-name  file-number-093-with-a-long-name\nfile-number-044-with-a-long-name  file-number-094-with-a-long-name\nfile-number-045-with-a-long-name  file-number-095-with-a-long-name\nfile-number-046-with-a-long-name  file-number-096-with-a-long-name\nfile-number-047-with-a-long-name  file-number-097-with-a-long-name\nfile-number-048-with-a-long-name  file-number-098-with-a-long-name\nfile-number-049-with-a-long-name  file-number-099-with-a-long-name\n'
```

- stderr bytes:

```python
b''
```

### width_80_many

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', 'many'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 0d6f9c77d61d2d96078fbc5440b0c31967f69bfdf7a185429d073eb18eb81681 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'f000  f008  f016  f024  f032  f040  f048  f056  f064  f072  f080  f088  f096\nf001  f009  f017  f025  f033  f041  f049  f057  f065  f073  f081  f089  f097\nf002  f010  f018  f026  f034  f042  f050  f058  f066  f074  f082  f090  f098\nf003  f011  f019  f027  f035  f043  f051  f059  f067  f075  f083  f091  f099\nf004  f012  f020  f028  f036  f044  f052  f060  f068  f076  f084  f092\nf005  f013  f021  f029  f037  f045  f053  f061  f069  f077  f085  f093\nf006  f014  f022  f030  f038  f046  f054  f062  f070  f078  f086  f094\nf007  f015  f023  f031  f039  f047  f055  f063  f071  f079  f087  f095\n'
```

- stderr bytes:

```python
b''
```

### width_x_names

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=x', 'names'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 370dcbe509e5c836d55b052ffb31dc73bc5fbff4193aafaf5a7c2b7f6aea134a `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "x" not valid for option --width (-w): invalid digit found in string\n'
```

### width_80_color_auto

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '--color=auto', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### width_80_color_always

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '--color=always', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` ec10700057f21e19c90b541db621e1060c56cfc8ecb4b8288eddf7633a492212 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\n\x1b[1;34mordered\x1b[0m\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\n\x1b[1;4;33mreadme\x1b[0m\n\x1b[1;4;33mREADME\x1b[0m\nrfc1\nrfc2\nrfc10\n\x1b[1;34mtie_Aa\x1b[0m\n\x1b[1;34mtie_dot\x1b[0m\n\x1b[1;34mtie_pic\x1b[0m\n\x1b[1;34mtie_readme\x1b[0m\n\x1b[1;34mtie_space\x1b[0m\nunder_score\nUPPER\nx-\nZ\nz\n\x1b[2m~\x1b[0m\n"
```

- stderr bytes:

```python
b''
```

### width_80_classify

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '-F=always', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 6c269d1557ed21b530060a07c15c58bb4edf93cb0aafa0b6f6660a715c932cf2 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-    alpha  broken@  dirlink@   link@   pipe|  sock=    sub.dir/\n--x  Beta   dir/     gamma.txt  noexec  prog*  spaced@  'tar get'\n"
```

- stderr bytes:

```python
b''
```

### width_80_icons

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '--icons', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` cdea5c12b045635f8c499b3966e45e1c52d8f5af6fb7d12d5bc854caa1353b3c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"\xf3\xb0\xa1\xaf -\n\xf3\xb0\xa1\xaf --\n\xf3\xb0\xa1\xaf --x\n\xf3\xb0\xa1\xaf -x\n\xef\x85\x9b ' .'\n\xf3\xb0\xa1\xaf 001\n\xf3\xb0\xa1\xaf 01\n\xf3\xb0\xa1\xaf 1\n\xf3\xb0\xa1\xaf 2\n\xf3\xb0\xa1\xaf 10\n\xf3\xb0\xa1\xaf a\n\xf3\xb0\xa1\xaf A\n\xf3\xb0\xa1\xaf ' a'\n\xf3\xb0\xa1\xaf 'a b'\n\xf3\xb0\xa1\xaf B\n\xf3\xb0\xa1\xaf b\n\xef\x85\x9b dot.\n\xee\x97\xbf ordered\n\xf3\xb0\xa1\xaf 'pic 4'\n\xf3\xb0\xa1\xaf pic4\n\xf3\xb0\xa1\xaf qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\n\xf3\xb0\xa1\xaf readme\n\xf3\xb0\x82\xba README\n\xf3\xb0\xa1\xaf rfc1\n\xf3\xb0\xa1\xaf rfc2\n\xf3\xb0\xa1\xaf rfc10\n\xee\x97\xbf tie_Aa\n\xee\x97\xbf tie_dot\n\xee\x97\xbf tie_pic\n\xee\x97\xbf tie_readme\n\xee\x97\xbf tie_space\n\xf3\xb0\xa1\xaf under_score\n\xf3\xb0\xa1\xaf UPPER\n\xf3\xb0\xa1\xaf x-\n\xf3\xb0\xa1\xaf Z\n\xf3\xb0\xa1\xaf z\n\xf3\xb0\xa1\xaf ~\n"
```

- stderr bytes:

```python
b''
```

### width_80_icons_always

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '--icons=always', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` cdea5c12b045635f8c499b3966e45e1c52d8f5af6fb7d12d5bc854caa1353b3c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"\xf3\xb0\xa1\xaf -\n\xf3\xb0\xa1\xaf --\n\xf3\xb0\xa1\xaf --x\n\xf3\xb0\xa1\xaf -x\n\xef\x85\x9b ' .'\n\xf3\xb0\xa1\xaf 001\n\xf3\xb0\xa1\xaf 01\n\xf3\xb0\xa1\xaf 1\n\xf3\xb0\xa1\xaf 2\n\xf3\xb0\xa1\xaf 10\n\xf3\xb0\xa1\xaf a\n\xf3\xb0\xa1\xaf A\n\xf3\xb0\xa1\xaf ' a'\n\xf3\xb0\xa1\xaf 'a b'\n\xf3\xb0\xa1\xaf B\n\xf3\xb0\xa1\xaf b\n\xef\x85\x9b dot.\n\xee\x97\xbf ordered\n\xf3\xb0\xa1\xaf 'pic 4'\n\xf3\xb0\xa1\xaf pic4\n\xf3\xb0\xa1\xaf qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\n\xf3\xb0\xa1\xaf readme\n\xf3\xb0\x82\xba README\n\xf3\xb0\xa1\xaf rfc1\n\xf3\xb0\xa1\xaf rfc2\n\xf3\xb0\xa1\xaf rfc10\n\xee\x97\xbf tie_Aa\n\xee\x97\xbf tie_dot\n\xee\x97\xbf tie_pic\n\xee\x97\xbf tie_readme\n\xee\x97\xbf tie_space\n\xf3\xb0\xa1\xaf under_score\n\xf3\xb0\xa1\xaf UPPER\n\xf3\xb0\xa1\xaf x-\n\xf3\xb0\xa1\xaf Z\n\xf3\xb0\xa1\xaf z\n\xf3\xb0\xa1\xaf ~\n"
```

- stderr bytes:

```python
b''
```

### width_40_icons_always

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '--icons=always', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` cdea5c12b045635f8c499b3966e45e1c52d8f5af6fb7d12d5bc854caa1353b3c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"\xf3\xb0\xa1\xaf -\n\xf3\xb0\xa1\xaf --\n\xf3\xb0\xa1\xaf --x\n\xf3\xb0\xa1\xaf -x\n\xef\x85\x9b ' .'\n\xf3\xb0\xa1\xaf 001\n\xf3\xb0\xa1\xaf 01\n\xf3\xb0\xa1\xaf 1\n\xf3\xb0\xa1\xaf 2\n\xf3\xb0\xa1\xaf 10\n\xf3\xb0\xa1\xaf a\n\xf3\xb0\xa1\xaf A\n\xf3\xb0\xa1\xaf ' a'\n\xf3\xb0\xa1\xaf 'a b'\n\xf3\xb0\xa1\xaf B\n\xf3\xb0\xa1\xaf b\n\xef\x85\x9b dot.\n\xee\x97\xbf ordered\n\xf3\xb0\xa1\xaf 'pic 4'\n\xf3\xb0\xa1\xaf pic4\n\xf3\xb0\xa1\xaf qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\n\xf3\xb0\xa1\xaf readme\n\xf3\xb0\x82\xba README\n\xf3\xb0\xa1\xaf rfc1\n\xf3\xb0\xa1\xaf rfc2\n\xf3\xb0\xa1\xaf rfc10\n\xee\x97\xbf tie_Aa\n\xee\x97\xbf tie_dot\n\xee\x97\xbf tie_pic\n\xee\x97\xbf tie_readme\n\xee\x97\xbf tie_space\n\xf3\xb0\xa1\xaf under_score\n\xf3\xb0\xa1\xaf UPPER\n\xf3\xb0\xa1\xaf x-\n\xf3\xb0\xa1\xaf Z\n\xf3\xb0\xa1\xaf z\n\xf3\xb0\xa1\xaf ~\n"
```

- stderr bytes:

```python
b''
```

### x_names

- cwd: ` <root> `
- argv: ` ['<ref>', '-x', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### width_80_x_names

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '-x', 'names'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### pty_default

- cwd: ` <root>/names `
- argv: ` ['<ref>', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 84b06db72ece430ea5d086b89f48e9e3ebd3684968d2c99b6cbc9429f0c6d19f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\r\n--\r\n--x\r\n-x\r\n' .'\r\n001\r\n01\r\n1\r\n2\r\n10\r\na\r\nA\r\n' a'\r\n'a b'\r\nB\r\nb\r\ndot.\r\n\x1b[1;34mordered\x1b[0m\r\n'pic 4'\r\npic4\r\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\r\n\x1b[1;4;33mreadme\x1b[0m\r\n\x1b[1;4;33mREADME\x1b[0m\r\nrfc1\r\nrfc2\r\nrfc10\r\n\x1b[1;34mtie_Aa\x1b[0m\r\n\x1b[1;34mtie_dot\x1b[0m\r\n\x1b[1;34mtie_pic\x1b[0m\r\n\x1b[1;34mtie_readme\x1b[0m\r\n\x1b[1;34mtie_space\x1b[0m\r\nunder_score\r\nUPPER\r\nx-\r\nZ\r\nz\r\n\x1b[2m~\x1b[0m\r\n"
```

- stderr bytes:

```python
b''
```

- note: ` stdout is a pty: TERM is unset, size from TIOCSWINSZ `

### pty_cols40

- cwd: ` <root>/names `
- argv: ` ['<ref>', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 84b06db72ece430ea5d086b89f48e9e3ebd3684968d2c99b6cbc9429f0c6d19f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\r\n--\r\n--x\r\n-x\r\n' .'\r\n001\r\n01\r\n1\r\n2\r\n10\r\na\r\nA\r\n' a'\r\n'a b'\r\nB\r\nb\r\ndot.\r\n\x1b[1;34mordered\x1b[0m\r\n'pic 4'\r\npic4\r\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\r\n\x1b[1;4;33mreadme\x1b[0m\r\n\x1b[1;4;33mREADME\x1b[0m\r\nrfc1\r\nrfc2\r\nrfc10\r\n\x1b[1;34mtie_Aa\x1b[0m\r\n\x1b[1;34mtie_dot\x1b[0m\r\n\x1b[1;34mtie_pic\x1b[0m\r\n\x1b[1;34mtie_readme\x1b[0m\r\n\x1b[1;34mtie_space\x1b[0m\r\nunder_score\r\nUPPER\r\nx-\r\nZ\r\nz\r\n\x1b[2m~\x1b[0m\r\n"
```

- stderr bytes:

```python
b''
```

### pty_cols120

- cwd: ` <root>/names `
- argv: ` ['<ref>', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 84b06db72ece430ea5d086b89f48e9e3ebd3684968d2c99b6cbc9429f0c6d19f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\r\n--\r\n--x\r\n-x\r\n' .'\r\n001\r\n01\r\n1\r\n2\r\n10\r\na\r\nA\r\n' a'\r\n'a b'\r\nB\r\nb\r\ndot.\r\n\x1b[1;34mordered\x1b[0m\r\n'pic 4'\r\npic4\r\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\r\n\x1b[1;4;33mreadme\x1b[0m\r\n\x1b[1;4;33mREADME\x1b[0m\r\nrfc1\r\nrfc2\r\nrfc10\r\n\x1b[1;34mtie_Aa\x1b[0m\r\n\x1b[1;34mtie_dot\x1b[0m\r\n\x1b[1;34mtie_pic\x1b[0m\r\n\x1b[1;34mtie_readme\x1b[0m\r\n\x1b[1;34mtie_space\x1b[0m\r\nunder_score\r\nUPPER\r\nx-\r\nZ\r\nz\r\n\x1b[2m~\x1b[0m\r\n"
```

- stderr bytes:

```python
b''
```

### pty_lines

- cwd: ` <root>/names `
- argv: ` ['<ref>', '-1', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 84b06db72ece430ea5d086b89f48e9e3ebd3684968d2c99b6cbc9429f0c6d19f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\r\n--\r\n--x\r\n-x\r\n' .'\r\n001\r\n01\r\n1\r\n2\r\n10\r\na\r\nA\r\n' a'\r\n'a b'\r\nB\r\nb\r\ndot.\r\n\x1b[1;34mordered\x1b[0m\r\n'pic 4'\r\npic4\r\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\r\n\x1b[1;4;33mreadme\x1b[0m\r\n\x1b[1;4;33mREADME\x1b[0m\r\nrfc1\r\nrfc2\r\nrfc10\r\n\x1b[1;34mtie_Aa\x1b[0m\r\n\x1b[1;34mtie_dot\x1b[0m\r\n\x1b[1;34mtie_pic\x1b[0m\r\n\x1b[1;34mtie_readme\x1b[0m\r\n\x1b[1;34mtie_space\x1b[0m\r\nunder_score\r\nUPPER\r\nx-\r\nZ\r\nz\r\n\x1b[2m~\x1b[0m\r\n"
```

- stderr bytes:

```python
b''
```

### pty_color_auto

- cwd: ` <root>/mixed `
- argv: ` ['<ref>', '--color=auto', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2654e8d989c283d6cc438bf1af232de7c542e029cf1725749640a672ae3945a4 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-    alpha  \x1b[31mbroken\x1b[0m  \x1b[36mdirlink\x1b[0m    \x1b[36mlink\x1b[0m    \x1b[33mpipe\x1b[0m  \x1b[1;31msock\x1b[0m    \x1b[1;34msub.dir\x1b[0m\r\n--x  Beta   \x1b[1;34mdir\x1b[0m     gamma.txt  noexec  \x1b[1;32mprog\x1b[0m  \x1b[36mspaced\x1b[0m  'tar get'\r\n"
```

- stderr bytes:

```python
b''
```

### pty_icons_always

- cwd: ` <root>/names `
- argv: ` ['<ref>', '--icons=always', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` a7bfddc0b1f61e1d1d46c96c34ba71541f9ac26f806b93c2ca9857e0cc3b46ae `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"\xf3\xb0\xa1\xaf -\r\n\xf3\xb0\xa1\xaf --\r\n\xf3\xb0\xa1\xaf --x\r\n\xf3\xb0\xa1\xaf -x\r\n\xef\x85\x9b ' .'\r\n\xf3\xb0\xa1\xaf 001\r\n\xf3\xb0\xa1\xaf 01\r\n\xf3\xb0\xa1\xaf 1\r\n\xf3\xb0\xa1\xaf 2\r\n\xf3\xb0\xa1\xaf 10\r\n\xf3\xb0\xa1\xaf a\r\n\xf3\xb0\xa1\xaf A\r\n\xf3\xb0\xa1\xaf ' a'\r\n\xf3\xb0\xa1\xaf 'a b'\r\n\xf3\xb0\xa1\xaf B\r\n\xf3\xb0\xa1\xaf b\r\n\xef\x85\x9b dot.\r\n\x1b[34m\xee\x97\xbf \x1b[1mordered\x1b[0m\r\n\xf3\xb0\xa1\xaf 'pic 4'\r\n\xf3\xb0\xa1\xaf pic4\r\n\xf3\xb0\xa1\xaf qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\r\n\x1b[33m\xf3\xb0\xa1\xaf \x1b[1;4mreadme\x1b[0m\r\n\x1b[33m\xf3\xb0\x82\xba \x1b[1;4mREADME\x1b[0m\r\n\xf3\xb0\xa1\xaf rfc1\r\n\xf3\xb0\xa1\xaf rfc2\r\n\xf3\xb0\xa1\xaf rfc10\r\n\x1b[34m\xee\x97\xbf \x1b[1mtie_Aa\x1b[0m\r\n\x1b[34m\xee\x97\xbf \x1b[1mtie_dot\x1b[0m\r\n\x1b[34m\xee\x97\xbf \x1b[1mtie_pic\x1b[0m\r\n\x1b[34m\xee\x97\xbf \x1b[1mtie_readme\x1b[0m\r\n\x1b[34m\xee\x97\xbf \x1b[1mtie_space\x1b[0m\r\n\xf3\xb0\xa1\xaf under_score\r\n\xf3\xb0\xa1\xaf UPPER\r\n\xf3\xb0\xa1\xaf x-\r\n\xf3\xb0\xa1\xaf Z\r\n\xf3\xb0\xa1\xaf z\r\n\xf3\xb0\xa1\xaf \x1b[2m~\x1b[0m\r\n"
```

- stderr bytes:

```python
b''
```

### pty_x

- cwd: ` <root>/names `
- argv: ` ['<ref>', '-x', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 84b06db72ece430ea5d086b89f48e9e3ebd3684968d2c99b6cbc9429f0c6d19f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\r\n--\r\n--x\r\n-x\r\n' .'\r\n001\r\n01\r\n1\r\n2\r\n10\r\na\r\nA\r\n' a'\r\n'a b'\r\nB\r\nb\r\ndot.\r\n\x1b[1;34mordered\x1b[0m\r\n'pic 4'\r\npic4\r\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\r\n\x1b[1;4;33mreadme\x1b[0m\r\n\x1b[1;4;33mREADME\x1b[0m\r\nrfc1\r\nrfc2\r\nrfc10\r\n\x1b[1;34mtie_Aa\x1b[0m\r\n\x1b[1;34mtie_dot\x1b[0m\r\n\x1b[1;34mtie_pic\x1b[0m\r\n\x1b[1;34mtie_readme\x1b[0m\r\n\x1b[1;34mtie_space\x1b[0m\r\nunder_score\r\nUPPER\r\nx-\r\nZ\r\nz\r\n\x1b[2m~\x1b[0m\r\n"
```

- stderr bytes:

```python
b''
```

## 14. Tree

### tree_T

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 6988074af909f08a092d967cea296e462227b464a138ff8c6872b20f9360b56f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tree\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 a.txt\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 broken -> missing\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 d1\n\xe2\x94\x82   \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 b.txt\n\xe2\x94\x82   \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d2\n\xe2\x94\x82       \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 c.txt\n\xe2\x94\x82       \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d3\n\xe2\x94\x82           \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d.txt\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 dirlink -> d1\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 emptydir\n\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 hidden\n'
```

- stderr bytes:

```python
b''
```

### tree_level1

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-L', '1', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 9c11eb00cbcb7cb6f5d681002b60037c5e3c00b456c0d56211da9720cdc9df32 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tree\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 a.txt\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 broken -> missing\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 d1\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 dirlink -> d1\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 emptydir\n\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 hidden\n'
```

- stderr bytes:

```python
b''
```

### tree_level2

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-L', '2', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` abacf580e5077296ba48b86dae8513e8e6e60c168def29d5abf5dac7a28f96f6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tree\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 a.txt\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 broken -> missing\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 d1\n\xe2\x94\x82   \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 b.txt\n\xe2\x94\x82   \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d2\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 dirlink -> d1\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 emptydir\n\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 hidden\n'
```

- stderr bytes:

```python
b''
```

### tree_level3

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-L', '3', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2e98d6e6e38db9c3737ea828adbdbb16ac5b98cee3d25980a44734a698c9861b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tree\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 a.txt\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 broken -> missing\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 d1\n\xe2\x94\x82   \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 b.txt\n\xe2\x94\x82   \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d2\n\xe2\x94\x82       \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 c.txt\n\xe2\x94\x82       \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d3\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 dirlink -> d1\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 emptydir\n\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 hidden\n'
```

- stderr bytes:

```python
b''
```

### tree_all

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-a', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 35d9cf16a2d69951870f70b56449a47b51c5a8c8aa90b20c4d60cd273ec94a21 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tree\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 .hiddenfile\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 a.txt\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 broken -> missing\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 d1\n\xe2\x94\x82   \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 b.txt\n\xe2\x94\x82   \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d2\n\xe2\x94\x82       \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 c.txt\n\xe2\x94\x82       \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d3\n\xe2\x94\x82           \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d.txt\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 dirlink -> d1\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 emptydir\n\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 hidden\n    \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 .secret\n'
```

- stderr bytes:

```python
b''
```

### tree_all_level3

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-a', '-L', '3', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 88c74400c694383399bd0632cd2688bb1dbc97f6853baab068df4c61f8597150 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tree\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 .hiddenfile\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 a.txt\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 broken -> missing\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 d1\n\xe2\x94\x82   \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 b.txt\n\xe2\x94\x82   \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d2\n\xe2\x94\x82       \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 c.txt\n\xe2\x94\x82       \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d3\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 dirlink -> d1\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 emptydir\n\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 hidden\n    \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 .secret\n'
```

- stderr bytes:

```python
b''
```

### tree_links

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', 'links'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 9a90aeb02ef947c20e500f920dfa2b246b47c9cee7bdab7c3e0e1da435769149 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"links\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 b -> real\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 chain -> realfile\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 dangling -> nowhere\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 exec -> exec-target\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 exec-target\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 file\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 loop1 -> loop2\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 loop2 -> loop1\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 real -> realdir\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 realdir\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 realdirlink -> realdir\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 realfile -> file\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 spaced -> 'tar get'\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 'tar get'\n\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 to_realdirlink -> realdirlink\n"
```

- stderr bytes:

```python
b''
```

- note: ` symlink directories are not descended by default `

### tree_links_all

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-a', 'links'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 9a90aeb02ef947c20e500f920dfa2b246b47c9cee7bdab7c3e0e1da435769149 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"links\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 b -> real\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 chain -> realfile\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 dangling -> nowhere\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 exec -> exec-target\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 exec-target\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 file\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 loop1 -> loop2\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 loop2 -> loop1\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 real -> realdir\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 realdir\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 realdirlink -> realdir\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 realfile -> file\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 spaced -> 'tar get'\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 'tar get'\n\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 to_realdirlink -> realdirlink\n"
```

- stderr bytes:

```python
b''
```

### tree_empty

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', 'empty'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 45fd2f9df187bc411dc80df5c4c4844ac00f4dd69bc69453f1893a5d143433d6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'empty\n'
```

- stderr bytes:

```python
b''
```

### tree_hidden_dir

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', 'tree/hidden'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5a865d3eaec620d9f238490dfae79d96a0c1a3624fc40f0bad6c84c5e51cf205 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tree/hidden\n'
```

- stderr bytes:

```python
b''
```

### tree_long

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 8143feca8201afd22fc3e173f3af7fe35360f899dac53ce512da9109c738443c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'drwxr-xr-x - 2020-09-13 12:26 tree\n.rw-r--r-- 2 2020-09-13 12:26 \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 a.txt\nlrwxrwxrwx - 2020-09-13 12:26 \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 broken -> missing\ndrwxr-xr-x - 2020-09-13 12:26 \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 d1\n.rw-r--r-- 2 2020-09-13 12:26 \xe2\x94\x82   \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 b.txt\ndrwxr-xr-x - 2020-09-13 12:26 \xe2\x94\x82   \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d2\n.rw-r--r-- 2 2020-09-13 12:26 \xe2\x94\x82       \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 c.txt\ndrwxr-xr-x - 2020-09-13 12:26 \xe2\x94\x82       \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d3\n.rw-r--r-- 2 2020-09-13 12:26 \xe2\x94\x82           \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d.txt\nlrwxrwxrwx - 2020-09-13 12:26 \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 dirlink -> d1\ndrwxr-xr-x - 2020-09-13 12:26 \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 emptydir\ndrwxr-xr-x - 2020-09-13 12:26 \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 hidden\n'
```

- stderr bytes:

```python
b''
```

### tree_color

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '--color=always', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` b7715e1bb8cfea3d24ab2528691f7f4118eff291982b23cd095376134f343897 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\x1b[1;34mtree\x1b[0m\n\x1b[1;90m\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \x1b[0ma.txt\n\x1b[1;90m\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \x1b[0m\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;90m\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \x1b[34md1\x1b[0m\n\x1b[1;90m\xe2\x94\x82   \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \x1b[0mb.txt\n\x1b[1;90m\xe2\x94\x82   \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 \x1b[34md2\x1b[0m\n\x1b[1;90m\xe2\x94\x82       \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \x1b[0mc.txt\n\x1b[1;90m\xe2\x94\x82       \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 \x1b[34md3\x1b[0m\n\x1b[1;90m\xe2\x94\x82           \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 \x1b[0md.txt\n\x1b[1;90m\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \x1b[0m\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34md1\x1b[0m\n\x1b[1;90m\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \x1b[34memptydir\x1b[0m\n\x1b[1;90m\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 \x1b[34mhidden\x1b[0m\n'
```

- stderr bytes:

```python
b''
```

### tree_icons

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '--icons=always', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` cb9678f34a2beb621fc6c5f70908bdcd12c46866b6bdc2f9516cb626b2e5ca13 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\xee\x97\xbf tree\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \xef\x85\x9c a.txt\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \xf3\xb0\xa1\xaf broken -> missing\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \xee\x97\xbf d1\n\xe2\x94\x82   \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \xef\x85\x9c b.txt\n\xe2\x94\x82   \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 \xee\x97\xbf d2\n\xe2\x94\x82       \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \xef\x85\x9c c.txt\n\xe2\x94\x82       \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 \xee\x97\xbf d3\n\xe2\x94\x82           \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 \xef\x85\x9c d.txt\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \xee\x97\xbf dirlink -> d1\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \xef\x84\x95 emptydir\n\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 \xf3\xb1\x9e\x9e hidden\n'
```

- stderr bytes:

```python
b''
```

### tree_spellout

- cwd: ` <root> `
- argv: ` ['<ref>', '--tree', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 6988074af909f08a092d967cea296e462227b464a138ff8c6872b20f9360b56f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tree\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 a.txt\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 broken -> missing\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 d1\n\xe2\x94\x82   \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 b.txt\n\xe2\x94\x82   \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d2\n\xe2\x94\x82       \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 c.txt\n\xe2\x94\x82       \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d3\n\xe2\x94\x82           \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d.txt\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 dirlink -> d1\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 emptydir\n\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 hidden\n'
```

- stderr bytes:

```python
b''
```

### tree_level0

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-L', '0', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f27e01fe7624cca3e69811a0bf9a4efd9dca9fd39f7a3a8f939cae0cfe8cdfb8 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tree\n'
```

- stderr bytes:

```python
b''
```

## 15. Environment variables

### TIME_STYLE_long_iso

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'times'] `
- env extras: ` {'TIME_STYLE': 'long-iso'} `
- exit: ` 0 `
- stdout sha256: ` 4aa1d9e8d6c1381e665f5c1aa64e37bb26be869001f02f44d0f2986c9cace651 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 1970-01-01 00:00 t1970\n.rw-r--r-- 2 1999-12-31 00:00 t1999\n.rw-r--r-- 2 2000-02-29 00:00 t2000\n.rw-r--r-- 2 2020-01-02 03:04 t2020\n.rw-r--r-- 2 2038-01-19 03:14 t2038\n.rw-r--r-- 2 2100-03-01 00:00 t2100\n.rw-r--r-- 2 -                tpre\n'
```

- stderr bytes:

```python
b''
```

### TIME_STYLE_full_iso

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', 'times'] `
- env extras: ` {'TIME_STYLE': 'full-iso'} `
- exit: ` 0 `
- stdout sha256: ` decdf01a24be17d495003829d44f4a58863f8634aeccf902c1e316407e080586 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 1970-01-01 00:00:00.000000000 +0000 t1970\n.rw-r--r-- 2 1999-12-31 00:00:00.000000000 +0000 t1999\n.rw-r--r-- 2 2000-02-29 00:00:00.000000000 +0000 t2000\n.rw-r--r-- 2 2020-01-02 03:04:05.000000000 +0000 t2020\n.rw-r--r-- 2 2038-01-19 03:14:07.000000000 +0000 t2038\n.rw-r--r-- 2 2100-03-01 00:00:00.000000000 +0000 t2100\n.rw-r--r-- 2 -                                   tpre\n'
```

- stderr bytes:

```python
b''
```

### TIME_STYLE_iso

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', 'times'] `
- env extras: ` {'TIME_STYLE': 'iso'} `
- exit: ` 0 `
- stdout sha256: ` fd5d29e62e55306fc89b2befa7c6d81bf0f37ea4b36a6a404db0d9b1d28f3346 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 1970-01-01 t1970\n.rw-r--r-- 2 1999-12-31 t1999\n.rw-r--r-- 2 2000-02-29 t2000\n.rw-r--r-- 2 2020-01-02 t2020\n.rw-r--r-- 2 2038-01-19 t2038\n.rw-r--r-- 2 2100-03-01 t2100\n.rw-r--r-- 2 -          tpre\n'
```

- stderr bytes:

```python
b''
```

### TIME_STYLE_bogus

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', 'times'] `
- env extras: ` {'TIME_STYLE': 'bogus'} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` cd75d104d7c2e6a83910105dd2dfd8b570b28a1080278430cbe113458a6cf797 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Option --time-style has no "bogus" setting (choices: default, long-iso, full-iso, iso, relative)\n'
```

### COLUMNS_40

- cwd: ` <root> `
- argv: ` ['<ref>', 'names'] `
- env extras: ` {'COLUMNS': '40'} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### EZA_GRID_ROWS

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', 'names'] `
- env extras: ` {'EZA_GRID_ROWS': '2'} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### EXA_GRID_ROWS

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', 'names'] `
- env extras: ` {'EXA_GRID_ROWS': '2'} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### EZA_ICONS_AUTO

- cwd: ` <root> `
- argv: ` ['<ref>', 'names'] `
- env extras: ` {'EZA_ICONS_AUTO': '1'} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### EXA_ICONS_AUTO

- cwd: ` <root> `
- argv: ` ['<ref>', 'names'] `
- env extras: ` {'EXA_ICONS_AUTO': '1'} `
- exit: ` 0 `
- stdout sha256: ` e54566c498e145b89245debecc212da40559b9f61c3c770470226d193d33b08c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--\n--x\n-x\n' .'\n001\n01\n1\n2\n10\na\nA\n' a'\n'a b'\nB\nb\ndot.\nordered\n'pic 4'\npic4\nqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\nreadme\nREADME\nrfc1\nrfc2\nrfc10\ntie_Aa\ntie_dot\ntie_pic\ntie_readme\ntie_space\nunder_score\nUPPER\nx-\nZ\nz\n~\n"
```

- stderr bytes:

```python
b''
```

### NO_COLOR

- cwd: ` <root> `
- argv: ` ['<ref>', 'mixed'] `
- env extras: ` {'NO_COLOR': '1'} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### LS_COLORS

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'LS_COLORS': 'di=01;34'} `
- exit: ` 0 `
- stdout sha256: ` 8dffaad805181466de7add40f4743e7cf20908ff09ebad41bdf1b0efc25ccc5e `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;34mdir\x1b[0m\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\ngamma.txt\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;34msub.dir\x1b[0m\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### EZA_COLORS

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'EZA_COLORS': 'di=01;32'} `
- exit: ` 0 `
- stdout sha256: ` ec4488dff1d9cbae19856a8cbd5232ad8f8e7d9d4fda69640bb7424a7887884d `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;32mdir\x1b[0m\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;32mdir\x1b[0m\ngamma.txt\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;32msub.dir\x1b[0m\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### EXA_COLORS

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'EXA_COLORS': 'di=01;33'} `
- exit: ` 0 `
- stdout sha256: ` 37815f60f76cb45207884bf1b01a82044bb77556f300aa5b9c982e62e1abdee0 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;33mdir\x1b[0m\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;33mdir\x1b[0m\ngamma.txt\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;33msub.dir\x1b[0m\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### TZ_New_York

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'times'] `
- env extras: ` {'TZ': 'America/New_York'} `
- exit: ` 0 `
- stdout sha256: ` 0c04f5bd7eba3fa9ad6dadd8f3e809dc7f6f372ef4370f622371da18acd586ad `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 1969-12-31 20:00 t1970\n.rw-r--r-- 2 1999-12-30 20:00 t1999\n.rw-r--r-- 2 2000-02-28 20:00 t2000\n.rw-r--r-- 2 2020-01-01 23:04 t2020\n.rw-r--r-- 2 2038-01-18 23:14 t2038\n.rw-r--r-- 2 2100-02-28 20:00 t2100\n.rw-r--r-- 2 -                tpre\n'
```

- stderr bytes:

```python
b''
```

### TZ_UTC

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'times'] `
- env extras: ` {'TZ': 'UTC'} `
- exit: ` 0 `
- stdout sha256: ` 4aa1d9e8d6c1381e665f5c1aa64e37bb26be869001f02f44d0f2986c9cace651 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 1970-01-01 00:00 t1970\n.rw-r--r-- 2 1999-12-31 00:00 t1999\n.rw-r--r-- 2 2000-02-29 00:00 t2000\n.rw-r--r-- 2 2020-01-02 03:04 t2020\n.rw-r--r-- 2 2038-01-19 03:14 t2038\n.rw-r--r-- 2 2100-03-01 00:00 t2100\n.rw-r--r-- 2 -                tpre\n'
```

- stderr bytes:

```python
b''
```

### EZA_STRICT

- cwd: ` <root> `
- argv: ` ['<ref>', '-a', '-a', 'mixed'] `
- env extras: ` {'EZA_STRICT': '1'} `
- exit: ` 0 `
- stdout sha256: ` f04c4a3e0281eb456556a9a345f2cdb4aa2b44ffb67c2169f3a8fb2bbbd41569 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n.\n..\n..double\n.hidden\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### EXA_STRICT

- cwd: ` <root> `
- argv: ` ['<ref>', '-a', '-a', 'mixed'] `
- env extras: ` {'EXA_STRICT': '1'} `
- exit: ` 0 `
- stdout sha256: ` f04c4a3e0281eb456556a9a345f2cdb4aa2b44ffb67c2169f3a8fb2bbbd41569 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n.\n..\n..double\n.hidden\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### EZA_STRICT_empty

- cwd: ` <root> `
- argv: ` ['<ref>', '-a', '-a', 'mixed'] `
- env extras: ` {'EZA_STRICT': ''} `
- exit: ` 0 `
- stdout sha256: ` f04c4a3e0281eb456556a9a345f2cdb4aa2b44ffb67c2169f3a8fb2bbbd41569 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n.\n..\n..double\n.hidden\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### EZA_DEBUG

- cwd: ` <root> `
- argv: ` ['<ref>', 'mixed'] `
- env extras: ` {'EZA_DEBUG': '1'} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` a356ae560926cff1966530440ab757477e480bb4cf23fe7196049038779ab2b9 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b'\x1b[38;5;243m[\x1b[0m\x1b[36mINFO\x1b[0m eza\x1b[38;5;243m]\x1b[0m matching on exa.run\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza\x1b[38;5;243m]\x1b[0m Running with options: Options {\n    dir_action: List,\n    filter: FileFilter {\n        sort_field: Name(\n            AaBbCc,\n        ),\n        flags: [],\n        dot_filter: JustFiles,\n        ignore_patterns: IgnorePatterns {\n            patterns: [],\n        },\n        git_ignore: Off,\n        no_symlinks: false,\n        show_symlinks: false,\n    },\n    view: View {\n        mode: Lines,\n        width: Automatic,\n        file_style: Options {\n            classify: JustFilenames,\n            show_icons: Never,\n            quote_style: QuoteSpaces,\n            embed_hyperlinks: Off,\n            absolute: Off,\n            is_a_tty: false,\n        },\n        deref_links: false,\n        follow_links: false,\n        total_size: false,\n    },\n    theme: Options {\n        use_colours: Automatic,\n        colour_scale: ColorScaleOptions {\n            mode: Gradient,\n            min_luminance: 40,\n            size: false,\n            age: false,\n        },\n        definitions: Definitions {\n            ls: None,\n            exa: None,\n        },\n        theme_config: None,\n    },\n    stdin: Stdin(\n        "\\n",\n    ),\n}\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed"\n\x1b[38;5;243m[\x1b[0m\x1b[36mINFO\x1b[0m eza::fs::dir\x1b[38;5;243m]\x1b[0m Reading directory "mixed"\n\x1b[38;5;243m[\x1b[0m\x1b[36mINFO\x1b[0m eza::fs::dir\x1b[38;5;243m]\x1b[0m Read directory success "mixed"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/-"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/--x"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/alpha"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/Beta"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Reading link "mixed/broken"\n\x1b[38;5;243m[\x1b[0m\x1b[31mERROR\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Error following link "missing": Os {\n    code: 2,\n    kind: NotFound,\n    message: "No such file or directory",\n}\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Reading link "mixed/dirlink"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/gamma.txt"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Reading link "mixed/link"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/noexec"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/prog"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Reading link "mixed/spaced"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/tar get"\n'
```

### EXA_DEBUG

- cwd: ` <root> `
- argv: ` ['<ref>', 'mixed'] `
- env extras: ` {'EXA_DEBUG': '1'} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` a356ae560926cff1966530440ab757477e480bb4cf23fe7196049038779ab2b9 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b'\x1b[38;5;243m[\x1b[0m\x1b[36mINFO\x1b[0m eza\x1b[38;5;243m]\x1b[0m matching on exa.run\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza\x1b[38;5;243m]\x1b[0m Running with options: Options {\n    dir_action: List,\n    filter: FileFilter {\n        sort_field: Name(\n            AaBbCc,\n        ),\n        flags: [],\n        dot_filter: JustFiles,\n        ignore_patterns: IgnorePatterns {\n            patterns: [],\n        },\n        git_ignore: Off,\n        no_symlinks: false,\n        show_symlinks: false,\n    },\n    view: View {\n        mode: Lines,\n        width: Automatic,\n        file_style: Options {\n            classify: JustFilenames,\n            show_icons: Never,\n            quote_style: QuoteSpaces,\n            embed_hyperlinks: Off,\n            absolute: Off,\n            is_a_tty: false,\n        },\n        deref_links: false,\n        follow_links: false,\n        total_size: false,\n    },\n    theme: Options {\n        use_colours: Automatic,\n        colour_scale: ColorScaleOptions {\n            mode: Gradient,\n            min_luminance: 40,\n            size: false,\n            age: false,\n        },\n        definitions: Definitions {\n            ls: None,\n            exa: None,\n        },\n        theme_config: None,\n    },\n    stdin: Stdin(\n        "\\n",\n    ),\n}\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed"\n\x1b[38;5;243m[\x1b[0m\x1b[36mINFO\x1b[0m eza::fs::dir\x1b[38;5;243m]\x1b[0m Reading directory "mixed"\n\x1b[38;5;243m[\x1b[0m\x1b[36mINFO\x1b[0m eza::fs::dir\x1b[38;5;243m]\x1b[0m Read directory success "mixed"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m deref_links false\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/-"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/--x"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/alpha"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/Beta"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Reading link "mixed/broken"\n\x1b[38;5;243m[\x1b[0m\x1b[31mERROR\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Error following link "missing": Os {\n    code: 2,\n    kind: NotFound,\n    message: "No such file or directory",\n}\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Reading link "mixed/dirlink"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/gamma.txt"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Reading link "mixed/link"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/noexec"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/prog"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Reading link "mixed/spaced"\n\x1b[38;5;243m[\x1b[0m\x1b[34mDEBUG\x1b[0m eza::fs::file\x1b[38;5;243m]\x1b[0m Statting file "mixed/tar get"\n'
```

### EZA_CONFIG_DIR

- cwd: ` <root> `
- argv: ` ['<ref>', 'mixed'] `
- env extras: ` {'EZA_CONFIG_DIR': '<root>/env/config'} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### XDG_CONFIG_HOME

- cwd: ` <root> `
- argv: ` ['<ref>', 'mixed'] `
- env extras: ` {'XDG_CONFIG_HOME': '<root>/env/xdg'} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### EZA_ICON_SPACING

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=always', 'mixed'] `
- env extras: ` {'EZA_ICON_SPACING': '2'} `
- exit: ` 0 `
- stdout sha256: ` 8d8f7130ad0bfd89aee9b441d2ab8adcbca7699d5b4b7765405595f2eff9bbcf `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"\xf3\xb0\xa1\xaf  -\n\xf3\xb0\xa1\xaf  --x\n\xf3\xb0\xa1\xaf  alpha\n\xf3\xb0\xa1\xaf  Beta\n\xf3\xb0\xa1\xaf  broken -> missing\n\xef\x84\x95  dir\n\xee\x97\xbf  dirlink -> dir\n\xef\x85\x9c  gamma.txt\n\xf3\xb0\xa1\xaf  link -> alpha\n\xf3\xb0\xa1\xaf  noexec\n\xf3\xb0\xa1\xaf  pipe\n\xf3\xb0\xa1\xaf  prog\n\xf3\xb0\xa1\xaf  sock\n\xf3\xb0\xa1\xaf  spaced -> 'tar get'\n\xef\x84\x95  sub.dir\n\xf3\xb0\xa1\xaf  'tar get'\n"
```

- stderr bytes:

```python
b''
```

### EXA_ICON_SPACING

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=always', 'mixed'] `
- env extras: ` {'EXA_ICON_SPACING': '2'} `
- exit: ` 0 `
- stdout sha256: ` 8d8f7130ad0bfd89aee9b441d2ab8adcbca7699d5b4b7765405595f2eff9bbcf `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"\xf3\xb0\xa1\xaf  -\n\xf3\xb0\xa1\xaf  --x\n\xf3\xb0\xa1\xaf  alpha\n\xf3\xb0\xa1\xaf  Beta\n\xf3\xb0\xa1\xaf  broken -> missing\n\xef\x84\x95  dir\n\xee\x97\xbf  dirlink -> dir\n\xef\x85\x9c  gamma.txt\n\xf3\xb0\xa1\xaf  link -> alpha\n\xf3\xb0\xa1\xaf  noexec\n\xf3\xb0\xa1\xaf  pipe\n\xf3\xb0\xa1\xaf  prog\n\xf3\xb0\xa1\xaf  sock\n\xf3\xb0\xa1\xaf  spaced -> 'tar get'\n\xef\x84\x95  sub.dir\n\xf3\xb0\xa1\xaf  'tar get'\n"
```

- stderr bytes:

```python
b''
```

### EZA_OVERRIDE_GIT

- cwd: ` <root> `
- argv: ` ['<ref>', 'mixed'] `
- env extras: ` {'EZA_OVERRIDE_GIT': '1'} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### EXA_OVERRIDE_GIT

- cwd: ` <root> `
- argv: ` ['<ref>', 'mixed'] `
- env extras: ` {'EXA_OVERRIDE_GIT': '1'} `
- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### EZA_MIN_LUMINANCE

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'EZA_MIN_LUMINANCE': '0.5'} `
- exit: ` 0 `
- stdout sha256: ` 8dffaad805181466de7add40f4743e7cf20908ff09ebad41bdf1b0efc25ccc5e `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;34mdir\x1b[0m\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\ngamma.txt\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;34msub.dir\x1b[0m\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

### EXA_MIN_LUMINANCE

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'EXA_MIN_LUMINANCE': '0.5'} `
- exit: ` 0 `
- stdout sha256: ` 8dffaad805181466de7add40f4743e7cf20908ff09ebad41bdf1b0efc25ccc5e `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;34mdir\x1b[0m\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\ngamma.txt\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;34msub.dir\x1b[0m\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

## 16. DT_UNKNOWN filesystem

### dt_unavailable

- skipped: ` no DT_UNKNOWN filesystem found among ['/proc', '/sys', '/dev/shm', '/run']; pass --dt-unknown DIR `
- note: ` the lazy-lstat fallback is not exercised on this host `

## 17. Invalid-UTF-8 names (sections 3.3/8.4; E2)

### list_invalid_dir

- cwd: ` <root> `
- argv: ` ['<ref>', 'invalid'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` b651c5477d80cf2e61b5da9374b59f1261a61b85f8567b3a8904e97b1219963f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'bad-\xef\xbf\xbd-name\n'
```

- stderr bytes:

```python
b''
```

- note: ` lossy U+FFFD display; E2 shape case `

### cwd_invalid_dir

- cwd: ` <root>/invalid `
- argv: ` ['<ref>', '.'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` b651c5477d80cf2e61b5da9374b59f1261a61b85f8567b3a8904e97b1219963f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'bad-\xef\xbf\xbd-name\n'
```

- stderr bytes:

```python
b''
```

- note: ` explicit . operand; a no-operand invocation is stdin mode `

### explicit_invalid_operand

- cwd: ` <root>/invalid `
- argv: ` ['<ref>', b'bad-\xff-name'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` b651c5477d80cf2e61b5da9374b59f1261a61b85f8567b3a8904e97b1219963f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'bad-\xef\xbf\xbd-name\n'
```

- stderr bytes:

```python
b''
```

### explicit_invalid_path

- cwd: ` <root> `
- argv: ` ['<ref>', b'invalid/bad-\xff-name'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f287bf44be2093dbe8d9048873bbbcad9d822e1d1e83972d9bcaf7842b8eda28 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'invalid/bad-\xef\xbf\xbd-name\n'
```

- stderr bytes:

```python
b''
```

### long_invalid

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'invalid'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5510b65889311deb2c7b098daf4b0fb75b8bb9da66a808a3294bc32b338c1358 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 4 2020-09-13 12:26 bad-\xef\xbf\xbd-name\n'
```

- stderr bytes:

```python
b''
```

### sort_name_invalid

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=name', 'invalid'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` b651c5477d80cf2e61b5da9374b59f1261a61b85f8567b3a8904e97b1219963f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'bad-\xef\xbf\xbd-name\n'
```

- stderr bytes:

```python
b''
```

### classify_invalid

- cwd: ` <root> `
- argv: ` ['<ref>', '-F=always', 'invalid'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` b651c5477d80cf2e61b5da9374b59f1261a61b85f8567b3a8904e97b1219963f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'bad-\xef\xbf\xbd-name\n'
```

- stderr bytes:

```python
b''
```

## 18. Stdin file-name mode and the no-operand behaviour

### empty_no_operand

- cwd: ` <root>/mixed `
- argv: ` ['<ref>'] `
- env extras: ` {} `
- stdin bytes:

```python
b''
```

- exit: ` 0 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b''
```

- note: ` no operands; stdin is a non-TTY pipe with an empty name list `

### dot_no_operand

- cwd: ` <root>/mixed `
- argv: ` ['<ref>'] `
- env extras: ` {} `
- stdin bytes:

```python
b'.\n'
```

- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` no operands; the stdin line "." is an operand and lists the cwd `

### names_no_operand

- cwd: ` <root>/mixed `
- argv: ` ['<ref>'] `
- env extras: ` {} `
- stdin bytes:

```python
b'alpha\nBeta\ndir\n'
```

- exit: ` 0 `
- stdout sha256: ` f769c3eaf207310d1d954f1bab89d9fab141fca75b67b5f7aa4b1537e7de50cb `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'alpha\nBeta\n\ndir:\n'
```

- stderr bytes:

```python
b''
```

- note: ` no operands; stdin lines become input operands `

### missing_no_operand

- cwd: ` <root>/mixed `
- argv: ` ['<ref>'] `
- env extras: ` {} `
- stdin bytes:

```python
b'missing\n'
```

- exit: ` 2 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 0fb9a28754aa4ff311917598c48cd52a9f34351d8b7f72616ef6d67a3f0d4bfb `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'"missing": No such file or directory (os error 2)\n'
```

- note: ` no operands; a missing stdin operand is an input-metadata error (exit 2) `

### dot_all_no_operand

- cwd: ` <root>/mixed `
- argv: ` ['<ref>', '-a'] `
- env extras: ` {} `
- stdin bytes:

```python
b'.\n'
```

- exit: ` 0 `
- stdout sha256: ` 5fb71556086c067d8b765d26cfac13da2c2075ea09d6299f2359593e25154b4a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n..double\n.hidden\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` flags apply to the stdin-provided operand `

### operand_ignores_stdin

- cwd: ` <root> `
- argv: ` ['<ref>', 'mixed'] `
- env extras: ` {} `
- stdin bytes:

```python
b'alpha\n'
```

- exit: ` 0 `
- stdout sha256: ` 5bf856f786e8d66dde7c286055d8ff7370d923d3934e39b44542732e0813e19a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken -> missing\ndir\ndirlink -> dir\ngamma.txt\nlink -> alpha\nnoexec\npipe\nprog\nsock\nspaced -> 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` with an operand present stdin is not read `

## P0 findings (not probe output)

### Zero operands read stdin when stdin is not a TTY (design 7.2)

Design section 7.2 says "Zero operands means `.`". The pinned binary instead
selects stdin file-name mode whenever stdin is not a terminal
(`src/options/stdin.rs` `FilesInput::deduce`; `src/main.rs` substitutes `.`
only for `FilesInput::Args`). With an empty stdin name list a no-operand
invocation prints nothing and exits 0, even in a non-empty directory and even
with `-a`/`-aa`.

Evidence: `default/mixed_no_operand` is empty; in the stdin group,
`stdin/empty_no_operand` is empty while `stdin/dot_no_operand` lists the cwd
and `stdin/names_no_operand` treats the stdin lines as input operands;
`stdin/operand_ignores_stdin` shows stdin is not read when an operand is
present.

Decision for E0: this is a documented non-TTY divergence. E0 does not
implement stdin file-name mode; no no-operand case is in the diff matrix
(`default/cwd_mixed` was removed) and the directory-listing intent is covered
with explicit `.` operands (`default/cwd_dot` and the tie/pty probes). The
stdin probes stay as evidence. The design note is not edited.

### E0 matrix exclusions (deliberate; P4 must not silently re-add these)

Removed from the differential matrix because the behaviour is outside E0.
Probe evidence remains where probes exist; the exceptions are called out per
item.

- `names/ordered_dot_name`, `names/ordered_dot_Name`,
  `names/ordered_dot_Name_reverse`: `--sort=.name`/`.Name` are rejected in E0
  (design 7.11). Only `--sort=.name` is probed (`names/names_dot_name`); there
  is no `.Name` probe.
- `quoting/no_quotes`, `quoting/lines_no_quotes`, `quoting/F_no_quotes`:
  `--no-quotes` is E1.
- `operands/multiple_files`, `operands/two_directories`: multiple operands are
  out of E0. No direct multiple-operand probe remains; the related evidence is
  the `automatic`-as-operand probes below (they exit 2).
- `classify/classify_separate_automatic`, `classify/F_separate_automatic`:
  `automatic` is not in WHEN, so eza treats it as an operand (multiple
  operands, out of E0).
- `sortkeys/inode`, `sortkeys/none`: out of E0 (design 7.11).
- `memory/tree`: tree is E1.
- `long/xattr`: xattr `@` is not in E0 (design 7.8/9.2/9.4); the
  `long/long_xattr` probe records the reference behaviour only.
- `errors/bad_level` is retagged own-only: `-T`/`-L` are E1, so E0 rejects
  them as unsupported options with E0's own message, not eza's numeric level
  value error. The `errors/bad_value_level` probe keeps the reference bytes.
- `long/default_user`, `long/no_user`: plain `-l` is not E0; only the
  restricted `-l --no-user --bytes --time-style=long-iso` (or `-n`) form is.
- `default/cwd_mixed`: no-operand stdin mode (above).
- `dirs/root_listing`, `sortkeys/ext_key_root`: redundant host-root live
  cases, removed; their probes were also removed in the host-noise cleanup
  (B1). The design-required root-parent live cases
  (`quoting/root_parent_tmp[_F]` for `eza /tmp`, `quoting/root_parent_root[_F]`
  for `eza /`) remain and do list `/` and `/tmp` in P4 (design 3.4 item 11).

Retagged (still in the matrix, deliberately):

- `errors/bad_time_style` is own-only: the reference accepts and ignores a bad
  `--time-style` in a non-long view; E0 rejects it (own usage error, exit 3).
  This is a recorded divergence.
- `long/aa_mixed`, `long/dot_d`, `long/dotdot_d`, `long/root_d` now use the
  restricted long invocation; `long/numeric_user` uses
  `-l -n --bytes --time-style=long-iso`. All five stay live-only because
  their rows carry host metadata.
- `long/dir_mode000` declares `requires='nonroot'`.
- `classify/F_d_sub_link`, `F_d_sub_dirlink`, `F_d_spaced_sub_link` pin
  cwd-vs-operand-parent target resolution (`sub/link -> target*`,
  `sub/dirlink -> real/`, `'x y/sub'/link -> target*`).

### Host-independent golden matrix (audit)

The quoting fixture has one all-whitespace name. Every all-whitespace name
compares Equal under natord (whitespace is skipped), so two of them made every
name-order listing of that tree readdir-order dependent (design 9.4). An
audit of every recordable tree, including the synthetic `.`/`..` names, found
no comparator-equal pair under `natcmp` or `natcmpFold` outside the live-only
`names`/`names/tie_*` trees; extension-key groups and type-rank groups contain
no equal names in recordable trees, and the size/time sort keys are distinct.

### Host noise and path leakage

- The absolute-target probes (`symlinks/absolute_tmp*`, `symlinks/absolute_root*`)
  now use `-d`, so they record the target display instead of listing the
  host's `/tmp` or `/`. The host-listing probes `dirs/root_listing` and
  `dotfiles/ext_key_root` were removed; no probe in this report lists a host
  directory.
- The probe runner normalizes all three temporary paths (fixture root,
  per-probe dir and the outer probe `mkdtemp` directory) to `<root>` and
  refuses a stream that still contains one after normalization, so a
  default-TMPDIR run cannot commit the harness path.

### Other P0 notes

- DT_UNKNOWN (section 3.4 item 16) remains explicitly **unverified** on this
  host: `/proc`, `/sys`, `/dev/shm` and `/run` all report d_type != 0 (scan
  counts in the host manifest). Re-run with `--dt-unknown DIR` on a
  filesystem that returns an unknown d_type.
- The section 3.1 required-flags probe is implemented in `tests/eza.py`
  (`check_reference_flags`): under `--require-reference` the pinned binary
  must accept `-l --no-user --bytes --time-style=long-iso
  --treat-dirs-as-files`, else setup exits 2.
- `--record` deliberately does not write `VERSION`, although design section
  9.6 lists it among `--record` outputs; keeping VERSION under the authorized
  reference step is the safer choice stated by the harness docstring.
- The xattr fixture is protected against ambient xattrs: `fixtures.build`
  reports `ambient_xattr` when a file we never label carries an xattr
  (SELinux), and every long-view golden case (detected from `-l`/`--long`,
  not just the `long` family: also `quoting/long`,
  `quoting/broken_relative_long`, `classify/F_long_links`,
  `dirs/d_long_file`, `dirs/d_long_dir`, `dirs/d_long_symlink`) then skips
  rather than absorb an `@` marker.
- The invalid-UTF-8 shape case stays probe-only until an E2 shape mode
  exists; there is no shape-comparison runner in P0.
- REFERENCE.md is not byte-for-byte reproducible: the host manifest records
  live host data (e.g. `/proc` entry counts). That is accepted.

Note for reviewers: `git diff --check` flags trailing whitespace in
`long/dir_mode000.stderr` (the permission summary line ends with a space).
That is an exact reference byte, not formatting; do not edit it.
