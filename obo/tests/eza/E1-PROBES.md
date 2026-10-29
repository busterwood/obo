# eza E1 re-probe report (P5b: colour, tree, --no-quotes)

Produced by `obo/tests/eza/probe.py` against the authorized pinned
reference. Every byte below is observed output; skipped probes have no
output. Temporary harness paths are normalized to `<root>`; probe
arguments show the reference as `<ref>`, while the report header and
host manifest record its absolute path and sha256. Absolute-target and
host-metadata probes are live/shape-only and are not committed goldens
(design section 9.4).

Reference: ` /tmp/eza-install-sim-F2odpb/eza `

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
- reference path: ` /tmp/eza-install-sim-F2odpb/eza `
- reference sha256: ` 10457dde35808517d09bcd3bfb6bc386000d06c2126e34b9071e09470a8f7cd8 `
- fixture capabilities: ` {'euid': 1000, 'umask': 18, 'pre_epoch': True, 'xattr': True, 'invalid_utf8': True, 'fifo': True, 'socket': True, 'hardlink': True, 'symlink': True, 'deep_path': True, 'nonroot': True, 'dev_full': True, 'ambient_xattr': False} `
- dt-unknown candidates: ` {'/proc': {'entries': 102, 'unknown': 0, 'sample': []}, '/sys': {'entries': 12, 'unknown': 0, 'sample': []}, '/dev/shm': {'entries': 7, 'unknown': 0, 'sample': []}, '/run': {'entries': 34, 'unknown': 0, 'sample': []}} `

## 19. E1 re-probes (P5b: colour, tree, --no-quotes)

### colour_palette_all

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', '-a', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 48b4ef02a434b96ac04aa55c19eee5766438368ed38aa5b55fd6b0889c17edcd `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\n..double\n.hidden\nalpha\nBeta\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;34mdir\x1b[0m\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\ngamma.txt\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;34msub.dir\x1b[0m\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` hidden files have no separate SGR; regular files stay plain `

### colour_palette_modes

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'modes'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 81c7ad08b7f1a75bced64bc92e092fda1b016973102b0c5391e61e94dc08b2d6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\x1b[1;34md000\x1b[0m\n\x1b[1;34md700\x1b[0m\n\x1b[1;34md755\x1b[0m\n\x1b[1;34md1777\x1b[0m\nm000\nm600\nm644\n\x1b[1;32mm700\x1b[0m\n\x1b[1;32mm755\x1b[0m\n\x1b[1;32mm1777\x1b[0m\n\x1b[1;32mm2755\x1b[0m\n\x1b[1;32mm4755\x1b[0m\n\x1b[33mpipe\x1b[0m\n\x1b[1;31msock\x1b[0m\n'
```

- stderr bytes:

```python
b''
```

- note: ` setuid/setgid/sticky names are executable green, directories blue; no special SGR for the mode bits in the name view `

### colour_filetypes

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'colours'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` fcd5b56353d49a72ee9fd158bd359d8200d22871561a0a5b3282cbc7a6f3ce84 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\x1b[2m#hash#\x1b[0m\n\x1b[35ma.png\x1b[0m\n\x1b[35mb.jpg\x1b[0m\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;35mc.mp4\x1b[0m\n\x1b[36md.mp3\x1b[0m\n\x1b[1;36me.flac\x1b[0m\n\x1b[36mexelink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;32mprog\x1b[0m\n\x1b[32mf.pdf\x1b[0m\n\x1b[31mg.gz\x1b[0m\n\x1b[31mh.zip\x1b[0m\n\x1b[2mi.tmp\x1b[0m\n\x1b[1;32mid_rsa\x1b[0m\n\x1b[33mj.o\x1b[0m\n\x1b[1;33mk.py\x1b[0m\n\x1b[1;33ml.c\x1b[0m\n\x1b[1;33mm.rs\x1b[0m\n\x1b[1;4;33mMakefile\x1b[0m\n\x1b[33mn.so\x1b[0m\nnoext\n\x1b[2mnoext~\x1b[0m\n\x1b[36mpnglink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[35ma.png\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;4;33mREADME\x1b[0m\n\x1b[1;32msecret.gpg\x1b[0m\n\x1b[35mupper.PNG\x1b[0m\n'
```

- stderr bytes:

```python
b''
```

- note: ` default file-type palette: readme/build names, image, video, music, lossless, crypto, document, compressed, temp, compiled, source, fi fallback `

### colour_ls_ext_order_a

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'ext'] `
- env extras: ` {'LS_COLORS': '*.gz=01;31:*.tar.gz=01;32'} `
- exit: ` 0 `
- stdout sha256: ` 53f7f7825633b935e717da019c1d40b140f3c4b92a50fb866db53f4a3991e585 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a.txt\nb\n\x1b[1;32mb.tar.gz\x1b[0m\n\x1b[1;34md\x1b[0m\ndot.\n\x1b[1;34mdx.\x1b[0m\n\x1b[1;34mdx.x\x1b[0m\nnoext\nsame.txt\n\x1b[1;34msub\x1b[0m\nZ.TXT\n'
```

- stderr bytes:

```python
b''
```

- note: ` later definition wins; a dotted key is a literal suffix `

### colour_ls_ext_order_b

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'ext'] `
- env extras: ` {'LS_COLORS': '*.tar.gz=01;32:*.gz=01;31'} `
- exit: ` 0 `
- stdout sha256: ` 93fff2677e3e4f489dfc7112f3b1490eceae84c2fbd622fd1e9b694f88e67c58 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a.txt\nb\n\x1b[1;31mb.tar.gz\x1b[0m\n\x1b[1;34md\x1b[0m\ndot.\n\x1b[1;34mdx.\x1b[0m\n\x1b[1;34mdx.x\x1b[0m\nnoext\nsame.txt\n\x1b[1;34msub\x1b[0m\nZ.TXT\n'
```

- stderr bytes:

```python
b''
```

### colour_ls_ext_case

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'ext'] `
- env extras: ` {'LS_COLORS': '*.TXT=01;31'} `
- exit: ` 0 `
- stdout sha256: ` 0664193e8ebf11fc81a42eaf60dc64c993ad8cc24d87ae5170a47ba7b814fb43 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a.txt\nb\n\x1b[31mb.tar.gz\x1b[0m\n\x1b[1;34md\x1b[0m\ndot.\n\x1b[1;34mdx.\x1b[0m\n\x1b[1;34mdx.x\x1b[0m\nnoext\nsame.txt\n\x1b[1;34msub\x1b[0m\n\x1b[1;31mZ.TXT\x1b[0m\n'
```

- stderr bytes:

```python
b''
```

### colour_ls_empty

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'LS_COLORS': 'di=:*.txt='} `
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

- note: ` an empty value is dropped before the SGR parser `

### colour_ls_invalid

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'LS_COLORS': 'di=bogus'} `
- exit: ` 0 `
- stdout sha256: ` 9af71ff65c190842a5b04de00f08c2dcd4ba484d31e1c7466f24b4ad6e17e535 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\ndir\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m dir\ngamma.txt\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` a non-SGR value overrides the slot with the empty style `

### colour_ls_sgr_order

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'LS_COLORS': 'di=31;1'} `
- exit: ` 0 `
- stdout sha256: ` d7020f76ed5ae7ef9102e77165fe433919a07d2959245e5b8f495ed3e6019750 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;31mdir\x1b[0m\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;31mdir\x1b[0m\ngamma.txt\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;31msub.dir\x1b[0m\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` canonical re-serialisation: bold before colour `

### colour_eza_over_ls

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'LS_COLORS': 'di=01;34', 'EZA_COLORS': 'di=01;32'} `
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

### colour_exa_fallback

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'LS_COLORS': 'di=01;34', 'EXA_COLORS': 'di=01;33'} `
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

### colour_eza_empty

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'LS_COLORS': 'di=01;34', 'EZA_COLORS': ''} `
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

- note: ` present-empty EZA_COLORS still displaces EXA_COLORS `

### colour_ln_target

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', 'mixed'] `
- env extras: ` {'LS_COLORS': 'ln=target'} `
- exit: ` 0 `
- stdout sha256: ` ca03eebfa7729692c6e11affcb4e9d468dfccfa2f3f958e45c85349883669e73 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;34mdir\x1b[0m\ndirlink \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\ngamma.txt\nlink \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\nspaced \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;34msub.dir\x1b[0m\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` the unsupported "target" value parses to the empty style `

### colour_no_quotes

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', '--no-quotes', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 88d619e6919bd702044c5c62f9d48d9bbbaac9d59b5b6ab7efc0b379f9e4c3d3 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\n\x1b[1;34mdir\x1b[0m\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\ngamma.txt\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\nnoexec\n\x1b[33mpipe\x1b[0m\n\x1b[1;32mprog\x1b[0m\n\x1b[1;31msock\x1b[0m\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\n\x1b[1;34msub.dir\x1b[0m\ntar get\n"
```

- stderr bytes:

```python
b''
```

- note: ` a valid target name keeps QuoteSpaces; the broken arrow is red `

### colour_no_color_always

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

- note: ` an explicit --color overrides the NO_COLOR default `

### tree_oneline_last

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-1', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 04d8c3c3147af96d38bfb36bc98627fa0e86726445202652540f19fd763373e2 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a.txt\nbroken -> missing\nd1\ndirlink -> d1\nemptydir\nhidden\n'
```

- stderr bytes:

```python
b''
```

- note: ` the last view flag wins: a lines listing, not a tree `

### tree_oneline_first

- cwd: ` <root> `
- argv: ` ['<ref>', '-1', '-T', 'tree'] `
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

### tree_d_treat

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-d', 'tree'] `
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

- note: ` without strict mode tree wins over -d (no conflict) `

### tree_error_aa

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-aa', 'tree'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` a47aa359916c763c7cd4cc90374c08d545c66c7bddab4f7461dcd55790607b86 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Option --tree is useless given --all --all\n'
```

### tree_oneline_aa_error

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-1', '-aa', 'tree'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` a47aa359916c763c7cd4cc90374c08d545c66c7bddab4f7461dcd55790607b86 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Option --tree is useless given --all --all\n'
```

- note: ` the tree/all-all error is independent of the view flag `

### tree_level_plus

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-L', '+2', 'tree'] `
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

### tree_level_ignored

- cwd: ` <root> `
- argv: ` ['<ref>', '-L', 'x', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 04d8c3c3147af96d38bfb36bc98627fa0e86726445202652540f19fd763373e2 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a.txt\nbroken -> missing\nd1\ndirlink -> d1\nemptydir\nhidden\n'
```

- stderr bytes:

```python
b''
```

- note: ` without -T the level is never parsed `

### tree_dirlink_slash

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', 'tree/dirlink/'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` ec2bc9dbb652322453aa4c4469d5700bad0e380c42c8267ed72d4e9d25a1ea83 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'tree/dirlink\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 b.txt\n\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d2\n    \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 c.txt\n    \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d3\n        \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 d.txt\n'
```

- stderr bytes:

```python
b''
```

- note: ` a trailing slash follows the operand; the root is a directory `

### tree_permission_child

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', 'modes'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f509a77eed0180c19671d9eb1b02ae502847ebe317b4340ef03843ba491ac236 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'modes\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 d000\n\xe2\x94\x82   \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 <Permission denied (os error 13)>\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 d700\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 d755\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 d1777\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 m000\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 m600\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 m644\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 m700\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 m755\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 m1777\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 m2755\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 m4755\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 pipe\n\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 sock\n'
```

- stderr bytes:

```python
b''
```

- note: ` an unreadable directory prints a red synthetic child `

### tree_long_permission

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-l', '--no-user', '--bytes', '--time-style=long-iso', 'locked/denied'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 65d001d09532e7a75b54c503f563d14b0a5f51ded41ed2d55f13c482ac37ad75 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'd--------- - 2020-09-13 12:26 locked/denied\n                              \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 <Permission denied (os error 13)>\n'
```

- stderr bytes:

```python
b''
```

- note: ` the error row is padded to the table total width `

### no_quotes

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

### no_quotes_classify

- cwd: ` <root> `
- argv: ` ['<ref>', '-F=always', '--no-quotes', 'quoting'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` b75a01845caa2044fd1f18689a38702b33f9c56f7bc7068a8857e77a8913f0e8 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b' \na b/\napos\'trophe\nback\\slash\nboth \' and "\nbrokenrel -> ../tar get\nbroken spaced -> a b/missing\ncr\\rname\nctl\\u{1}name\nctl target -> ctl\\u{1}target\ndel\\u{7f}name\ndquote"name\nesc\\u{1b}name\nlead space\nnl\\nname\nplain\ntab\\tname\ntrail \nvalid spaced -> a b/target\nwith space\n'
```

- stderr bytes:

```python
b''
```

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

### A parented `..` operand keeps its parent prefix (design 7.2)

Design 7.2 says `.` and `..` operands display as themselves. The pinned binary
instead applies the same parent-prefix rule as any other operand: `-d sub/..`
prints `sub/..`, `-d 'x y/..'` prints `'x y'/..`, `-d /..` prints `/..`, and
`-d /../..` prints `/../..`; only an empty parent (`-d ..`, `-d .`, `-d ./`)
prints the bare name. Evidence: `dirs/d_sub_dotdot`, `dirs/d_spaced_dotdot`,
`dirs/d_dot`, `dirs/d_dotdot`, `dirs/d_dot_slash_dotdot`,
`dirs/d_sub_dotdot_slash`, `dirs/d_root_dotdot`,
`dirs/d_root_dotdot_dotdot`, `dirs/d_tmp_dotdot`, `dirs/d_slash_slash` and
`dirs/d_slash_slash_dotdot`. P4 implements the probe rule.

### The synthetic `.` on a symlink-to-directory operand is the link (design 7.3)

Design 7.3 models the synthetic `.` row as a directory whose path is the
operand. The pinned binary instead reads `lstat(operand)`, so for a
symlink-to-directory operand the `.` row is the symlink: `-aa tree/dirlink`
prints `. -> d1` followed by the target directory's children, type sorting
gives the dot the link rank (`..`, `d2`, `b.txt`, `. -> d1`), `-D` excludes it
and `-f` excludes both dots, and the restricted long row is `lrwxrwxrwx ... .
-> d1`. `..` is `lstat(join(operand,".."))`.

Target display and classification reorient the raw target against the operand
path itself, not its parent: `-aa links/realdirlink` prints `. -> realdir`
with no marker under `-F=always` (the operand-base stat fails), while
`-aa selflink/d/self` prints `. -> .` and marks/groups it as a directory, and
an absolute or `self`-style target resolves and is marked. A trailing slash
resolves the operand first, so `.` is then a directory row. Evidence:
`symlinks/aa_symlink_dir*`, `symlinks/aa_selflink*`, plus the P4 committed
counterparts `default/aa_symlink_dir*`, `names/aa_*_symlink_dir`,
`sortkeys/*_aa_symlink_dir`, `classify/F_aa_*` and `long/aa_*`. P4 implements
the probe rule.

### `-r` is a stable descending comparison (design 5.3/7.5)

Design 5.3/7.5 say eza sorts ascending and then reverses the whole vector,
which would reverse ties. The pinned binary compares descending with a stable
sort instead: comparator-equal entries keep their readdir order. Evidence:
`sort/size_reverse_ties` and `sort/modified_reverse_ties` on the mixed tree
(equal sizes/mtimes), and `names/tie_Aa_reverse`/`names/tie_pic_reverse`
(fold-tied and both-comparator-tied names). Distinct keys sort descending
either way, so the committed goldens (all tie-free) are unchanged. P4 uses a
stable descending comparison and keeps tie cases live-only.

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
