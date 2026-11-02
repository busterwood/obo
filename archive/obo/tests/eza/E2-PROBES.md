# eza P6b E2 re-probe report

Produced by `obo/tests/eza/probe.py` against the authorized pinned
reference. Every byte below is observed output; skipped probes have no
output. Temporary harness paths are normalized to `<root>`; probe
arguments show the reference as `<ref>`, while the report header and
host manifest record its absolute path and sha256. Absolute-target and
host-metadata probes are live/shape-only and are not committed goldens
(design section 9.4).

Reference: ` /tmp/eza-tar-test2/eza `

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
- reference path: ` /tmp/eza-tar-test2/eza `
- reference sha256: ` 10457dde35808517d09bcd3bfb6bc386000d06c2126e34b9071e09470a8f7cd8 `
- fixture capabilities: ` {'euid': 1000, 'umask': 18, 'pre_epoch': True, 'xattr': True, 'invalid_utf8': True, 'fifo': True, 'socket': True, 'hardlink': True, 'symlink': True, 'deep_path': True, 'nonroot': True, 'dev_full': True, 'ambient_xattr': False} `
- dt-unknown candidates: ` {'/proc': {'entries': 98, 'unknown': 0, 'sample': []}, '/sys': {'entries': 12, 'unknown': 0, 'sample': []}, '/dev/shm': {'entries': 7, 'unknown': 0, 'sample': []}, '/run': {'entries': 34, 'unknown': 0, 'sample': []}} `

## 20. E2 re-probes (P6b: grid, width sources, UTF-8, lossy names)

### width_20_grid10

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=20', '--icons=never', 'grid10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 0bb1eb58e102f601407b113d9fe8652cbb99487705459236a4bc80467f3fe784 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g0  g2  g4  g6  g8\ng1  g3  g5  g7  g9\n'
```

- stderr bytes:

```python
b''
```

### width_40_grid10

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '--icons=never', 'grid10'] `
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

### width_20_grid100

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=20', '--icons=never', 'grid100'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 5337fd6b50998cd4cb6b0dff4f02e9d35b1239ca1c2526c5687ff418bb9c66ec `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g00  g25  g50  g75\ng01  g26  g51  g76\ng02  g27  g52  g77\ng03  g28  g53  g78\ng04  g29  g54  g79\ng05  g30  g55  g80\ng06  g31  g56  g81\ng07  g32  g57  g82\ng08  g33  g58  g83\ng09  g34  g59  g84\ng10  g35  g60  g85\ng11  g36  g61  g86\ng12  g37  g62  g87\ng13  g38  g63  g88\ng14  g39  g64  g89\ng15  g40  g65  g90\ng16  g41  g66  g91\ng17  g42  g67  g92\ng18  g43  g68  g93\ng19  g44  g69  g94\ng20  g45  g70  g95\ng21  g46  g71  g96\ng22  g47  g72  g97\ng23  g48  g73  g98\ng24  g49  g74  g99\n'
```

- stderr bytes:

```python
b''
```

### width_40_grid100

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '--icons=never', 'grid100'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` c392301c377d4aac3fe280f34faaaafb7dee701eefa8877083efcaca653f7ec6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g00  g13  g26  g39  g52  g65  g78  g91\ng01  g14  g27  g40  g53  g66  g79  g92\ng02  g15  g28  g41  g54  g67  g80  g93\ng03  g16  g29  g42  g55  g68  g81  g94\ng04  g17  g30  g43  g56  g69  g82  g95\ng05  g18  g31  g44  g57  g70  g83  g96\ng06  g19  g32  g45  g58  g71  g84  g97\ng07  g20  g33  g46  g59  g72  g85  g98\ng08  g21  g34  g47  g60  g73  g86  g99\ng09  g22  g35  g48  g61  g74  g87\ng10  g23  g36  g49  g62  g75  g88\ng11  g24  g37  g50  g63  g76  g89\ng12  g25  g38  g51  g64  g77  g90\n'
```

- stderr bytes:

```python
b''
```

### width_80_grid100

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '--icons=never', 'grid100'] `
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

### width_120_grid100

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=120', '--icons=never', 'grid100'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 4d839e69d04ea9e520f519f00b005fdceaed791f3e03847712b16014d2d1f9f5 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g00  g05  g10  g15  g20  g25  g30  g35  g40  g45  g50  g55  g60  g65  g70  g75  g80  g85  g90  g95\ng01  g06  g11  g16  g21  g26  g31  g36  g41  g46  g51  g56  g61  g66  g71  g76  g81  g86  g91  g96\ng02  g07  g12  g17  g22  g27  g32  g37  g42  g47  g52  g57  g62  g67  g72  g77  g82  g87  g92  g97\ng03  g08  g13  g18  g23  g28  g33  g38  g43  g48  g53  g58  g63  g68  g73  g78  g83  g88  g93  g98\ng04  g09  g14  g19  g24  g29  g34  g39  g44  g49  g54  g59  g64  g69  g74  g79  g84  g89  g94  g99\n'
```

- stderr bytes:

```python
b''
```

### width_20_gridlong10

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=20', '--icons=never', 'gridlong10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 3708c4f7ab6513c645a5c593eb31301cb71a0d34e2f9bc7220086b4b9b5cda48 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'file-number-00-with-a-long-name\nfile-number-01-with-a-long-name\nfile-number-02-with-a-long-name\nfile-number-03-with-a-long-name\nfile-number-04-with-a-long-name\nfile-number-05-with-a-long-name\nfile-number-06-with-a-long-name\nfile-number-07-with-a-long-name\nfile-number-08-with-a-long-name\nfile-number-09-with-a-long-name\n'
```

- stderr bytes:

```python
b''
```

- note: ` the widest name does not fit: one full name per line, no truncation `

### width_40_gridlong10

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '--icons=never', 'gridlong10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 3708c4f7ab6513c645a5c593eb31301cb71a0d34e2f9bc7220086b4b9b5cda48 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'file-number-00-with-a-long-name\nfile-number-01-with-a-long-name\nfile-number-02-with-a-long-name\nfile-number-03-with-a-long-name\nfile-number-04-with-a-long-name\nfile-number-05-with-a-long-name\nfile-number-06-with-a-long-name\nfile-number-07-with-a-long-name\nfile-number-08-with-a-long-name\nfile-number-09-with-a-long-name\n'
```

- stderr bytes:

```python
b''
```

### width_40_gridlong100

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '--icons=never', 'gridlong100'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 576a80614b104764d285f6c6a9b10588c69a1d3a5f3001b36479f52237fa20d7 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'file-number-000-with-a-long-name\nfile-number-001-with-a-long-name\nfile-number-002-with-a-long-name\nfile-number-003-with-a-long-name\nfile-number-004-with-a-long-name\nfile-number-005-with-a-long-name\nfile-number-006-with-a-long-name\nfile-number-007-with-a-long-name\nfile-number-008-with-a-long-name\nfile-number-009-with-a-long-name\nfile-number-010-with-a-long-name\nfile-number-011-with-a-long-name\nfile-number-012-with-a-long-name\nfile-number-013-with-a-long-name\nfile-number-014-with-a-long-name\nfile-number-015-with-a-long-name\nfile-number-016-with-a-long-name\nfile-number-017-with-a-long-name\nfile-number-018-with-a-long-name\nfile-number-019-with-a-long-name\nfile-number-020-with-a-long-name\nfile-number-021-with-a-long-name\nfile-number-022-with-a-long-name\nfile-number-023-with-a-long-name\nfile-number-024-with-a-long-name\nfile-number-025-with-a-long-name\nfile-number-026-with-a-long-name\nfile-number-027-with-a-long-name\nfile-number-028-with-a-long-name\nfile-number-029-with-a-long-name\nfile-number-030-with-a-long-name\nfile-number-031-with-a-long-name\nfile-number-032-with-a-long-name\nfile-number-033-with-a-long-name\nfile-number-034-with-a-long-name\nfile-number-035-with-a-long-name\nfile-number-036-with-a-long-name\nfile-number-037-with-a-long-name\nfile-number-038-with-a-long-name\nfile-number-039-with-a-long-name\nfile-number-040-with-a-long-name\nfile-number-041-with-a-long-name\nfile-number-042-with-a-long-name\nfile-number-043-with-a-long-name\nfile-number-044-with-a-long-name\nfile-number-045-with-a-long-name\nfile-number-046-with-a-long-name\nfile-number-047-with-a-long-name\nfile-number-048-with-a-long-name\nfile-number-049-with-a-long-name\nfile-number-050-with-a-long-name\nfile-number-051-with-a-long-name\nfile-number-052-with-a-long-name\nfile-number-053-with-a-long-name\nfile-number-054-with-a-long-name\nfile-number-055-with-a-long-name\nfile-number-056-with-a-long-name\nfile-number-057-with-a-long-name\nfile-number-058-with-a-long-name\nfile-number-059-with-a-long-name\nfile-number-060-with-a-long-name\nfile-number-061-with-a-long-name\nfile-number-062-with-a-long-name\nfile-number-063-with-a-long-name\nfile-number-064-with-a-long-name\nfile-number-065-with-a-long-name\nfile-number-066-with-a-long-name\nfile-number-067-with-a-long-name\nfile-number-068-with-a-long-name\nfile-number-069-with-a-long-name\nfile-number-070-with-a-long-name\nfile-number-071-with-a-long-name\nfile-number-072-with-a-long-name\nfile-number-073-with-a-long-name\nfile-number-074-with-a-long-name\nfile-number-075-with-a-long-name\nfile-number-076-with-a-long-name\nfile-number-077-with-a-long-name\nfile-number-078-with-a-long-name\nfile-number-079-with-a-long-name\nfile-number-080-with-a-long-name\nfile-number-081-with-a-long-name\nfile-number-082-with-a-long-name\nfile-number-083-with-a-long-name\nfile-number-084-with-a-long-name\nfile-number-085-with-a-long-name\nfile-number-086-with-a-long-name\nfile-number-087-with-a-long-name\nfile-number-088-with-a-long-name\nfile-number-089-with-a-long-name\nfile-number-090-with-a-long-name\nfile-number-091-with-a-long-name\nfile-number-092-with-a-long-name\nfile-number-093-with-a-long-name\nfile-number-094-with-a-long-name\nfile-number-095-with-a-long-name\nfile-number-096-with-a-long-name\nfile-number-097-with-a-long-name\nfile-number-098-with-a-long-name\nfile-number-099-with-a-long-name\n'
```

- stderr bytes:

```python
b''
```

### width_80_gridlong100

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '--icons=never', 'gridlong100'] `
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

### width_20_gridmix

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=20', '--icons=never', 'gridmix'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` ecca61a3d44b454d6d3f6648b7c22bade2ce11db8249bd11a03e777e0b352d0a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a      ffffff\nbb     ggggggg\nccc    hhhhhhhh\ndddd   iiiiiiiii\neeeee  jjjjjjjjjj\n'
```

- stderr bytes:

```python
b''
```

### width_40_gridmix

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '--icons=never', 'gridmix'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 28b5cded98d7ac08b6d0c91c18dd3e0fb0d62bd6c5dd884fd3174589a387c8b6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a   ccc   eeeee   ggggggg   iiiiiiiii\nbb  dddd  ffffff  hhhhhhhh  jjjjjjjjjj\n'
```

- stderr bytes:

```python
b''
```

### width_1_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=1', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 9261105d2889007c7d43d3dd79e86e8ccb1cfc3008101a4ea75eda3a47d5296f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken\ndir\ndirlink\ngamma.txt\nlink\nnoexec\npipe\nprog\nsock\nspaced\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` width 1 is a real grid: one cell per line, no link targets `

### width_2_gridmix

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=2', '--icons=never', 'gridmix'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 68c4ac6ba4bb6a7dc94a19da5ab5af32ecf0216e0db88ca222d0d8d764a1b1dc `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a\nbb\nccc\ndddd\neeeee\nffffff\nggggggg\nhhhhhhhh\niiiiiiiii\njjjjjjjjjj\n'
```

- stderr bytes:

```python
b''
```

### width_40_x_gridmix

- cwd: ` <root> `
- argv: ` ['<ref>', '-x', '--width=40', '--icons=never', 'gridmix'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 7424002e3ab0aaadfa55319c11cd126107db92c5edbd06727c138e8e5e3cb8ea `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a          bb          ccc      dddd\neeeee      ffffff      ggggggg  hhhhhhhh\niiiiiiiii  jjjjjjjjjj\n'
```

- stderr bytes:

```python
b''
```

- note: ` -x fills left to right `

### width_20_x_grid10

- cwd: ` <root> `
- argv: ` ['<ref>', '-x', '--width=20', '--icons=never', 'grid10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 515c661c456557173689c1b349cfeeb0539d6d771fab6e3222b375a5f995e73d `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g0  g1  g2  g3  g4\ng5  g6  g7  g8  g9\n'
```

- stderr bytes:

```python
b''
```

### width_40_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2fa2b08ec58c6a5250a3700aa3e0919e46a87af929797b63ef0d530f519afad4 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-      broken     link    sock\n--x    dir        noexec  spaced\nalpha  dirlink    pipe    sub.dir\nBeta   gamma.txt  prog    'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ``grid never shows ` -> target```

### width_40_quoting

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '--icons=never', 'quoting'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 0047e09e8fd173488c47b497c0771eccc04c8ae866c39a2ff30434c8ad344b55 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\' \'              del\\u{7f}name\n\'a b\'            dquote"name\n"apos\'trophe"    esc\\u{1b}name\nback\\slash       \'lead space\'\n"both \' and ""   nl\\nname\nbrokenrel        plain\n\'broken spaced\'  tab\\tname\ncr\\rname         \'trail \'\nctl\\u{1}name     \'valid spaced\'\n\'ctl target\'     \'with space\'\n'
```

- stderr bytes:

```python
b''
```

- note: ` cells are quoted/escaped names `

### G_fallback_grid100

- cwd: ` <root> `
- argv: ` ['<ref>', '-G', '--icons=never', 'grid100'] `
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

- note: ` explicit -G with no width uses the 80-column fallback `

### G_oneline_last

- cwd: ` <root> `
- argv: ` ['<ref>', '-G', '-1', '--icons=never', 'mixed'] `
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

### oneline_then_G

- cwd: ` <root> `
- argv: ` ['<ref>', '-1', '-G', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` d28b1351a88cb672b17fc44cbac79d5999790ef4f11199f83fc21a312590ec41 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-    alpha  broken  dirlink    link    pipe  sock    sub.dir\n--x  Beta   dir     gamma.txt  noexec  prog  spaced  'tar get'\n"
```

- stderr bytes:

```python
b''
```

### tree_then_G

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-G', '--icons=never', 'grid10'] `
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

- note: ` the last of -G/-T wins `

### G_then_tree

- cwd: ` <root> `
- argv: ` ['<ref>', '-G', '-T', '--icons=never', 'grid10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 6a613e48ac7faffe9413df84e087096cdea0fc410978dec3d66c08055b56c832 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'grid10\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 g0\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 g1\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 g2\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 g3\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 g4\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 g5\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 g6\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 g7\n\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 g8\n\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 g9\n'
```

- stderr bytes:

```python
b''
```

### long_oneline_G

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '-G', '-1', '--icons=never', 'mixed'] `
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

### lG_grid10

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '-G', '--no-user', '--bytes', '--time-style=long-iso', '--icons=never', 'grid10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 7e617b7ec1b9cbaebcef91110757684292c362eb3355252782dcb6becd09773b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 2020-09-13 12:26 g0\n.rw-r--r-- 2 2020-09-13 12:26 g1\n.rw-r--r-- 2 2020-09-13 12:26 g2\n.rw-r--r-- 2 2020-09-13 12:26 g3\n.rw-r--r-- 2 2020-09-13 12:26 g4\n.rw-r--r-- 2 2020-09-13 12:26 g5\n.rw-r--r-- 2 2020-09-13 12:26 g6\n.rw-r--r-- 2 2020-09-13 12:26 g7\n.rw-r--r-- 2 2020-09-13 12:26 g8\n.rw-r--r-- 2 2020-09-13 12:26 g9\n'
```

- stderr bytes:

```python
b''
```

- note: ` eza GridDetails: long rows in grid columns; out of E2 scope and rejected by this build `

### Gl_grid10

- cwd: ` <root> `
- argv: ` ['<ref>', '-G', '-l', '--no-user', '--bytes', '--time-style=long-iso', '--icons=never', 'grid10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 7e617b7ec1b9cbaebcef91110757684292c362eb3355252782dcb6becd09773b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'.rw-r--r-- 2 2020-09-13 12:26 g0\n.rw-r--r-- 2 2020-09-13 12:26 g1\n.rw-r--r-- 2 2020-09-13 12:26 g2\n.rw-r--r-- 2 2020-09-13 12:26 g3\n.rw-r--r-- 2 2020-09-13 12:26 g4\n.rw-r--r-- 2 2020-09-13 12:26 g5\n.rw-r--r-- 2 2020-09-13 12:26 g6\n.rw-r--r-- 2 2020-09-13 12:26 g7\n.rw-r--r-- 2 2020-09-13 12:26 g8\n.rw-r--r-- 2 2020-09-13 12:26 g9\n'
```

- stderr bytes:

```python
b''
```

### G_l_T

- cwd: ` <root> `
- argv: ` ['<ref>', '-G', '-l', '-T', '--no-user', '--bytes', '--time-style=long-iso', '--icons=never', 'grid2'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 8783ecce89edb8d7cb96380adb75647a5af9e8a06aa2c486e2f0fa52c1ef3e88 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'drwxr-xr-x - 2020-09-13 12:26 grid2\n.rw-r--r-- 2 2020-09-13 12:26 \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 g0\n.rw-r--r-- 2 2020-09-13 12:26 \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 g1\n'
```

- stderr bytes:

```python
b''
```

- note: ` the last of -G/-T is -T: a long tree `

### l_G_T

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '-G', '-T', '--no-user', '--bytes', '--time-style=long-iso', '--icons=never', 'grid2'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 8783ecce89edb8d7cb96380adb75647a5af9e8a06aa2c486e2f0fa52c1ef3e88 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'drwxr-xr-x - 2020-09-13 12:26 grid2\n.rw-r--r-- 2 2020-09-13 12:26 \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 g0\n.rw-r--r-- 2 2020-09-13 12:26 \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 g1\n'
```

- stderr bytes:

```python
b''
```

### width_0_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=0', '--icons=never', 'mixed'] `
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

- note: ` --width=0 is Automatic: non-TTY Lines, targets shown `

### G_width_0

- cwd: ` <root> `
- argv: ` ['<ref>', '-G', '--width=0', '--icons=never', 'grid10'] `
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

### width_plus_40

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=+40', '--icons=never', 'grid10'] `
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

### width_leading_zero

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=040', '--icons=never', 'grid10'] `
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

### width_short_attached

- cwd: ` <root> `
- argv: ` ['<ref>', '-w40', '--icons=never', 'grid10'] `
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

### width_short_equals

- cwd: ` <root> `
- argv: ` ['<ref>', '-w=40', '--icons=never', 'grid10'] `
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

### width_short_missing

- cwd: ` <root> `
- argv: ` ['<ref>', '-w'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` fef5562aa0919af3f42b465b6d4b4ba45ce9f1a2339ddab167583cb21f2af416 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Flag -w needs a value\n'
```

### width_short_next

- cwd: ` <root> `
- argv: ` ['<ref>', '-w', 'grid10'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 0b822d9ed276efa03d8fec4ae62fbe5874c7da5267d7cbeaa48db4f434f2cfb5 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "grid10" not valid for option --width (-w): invalid digit found in string\n'
```

- note: ` the next token is the value, not an operand `

### width_x

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=x', 'grid10'] `
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

### width_negative

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=-1', 'grid10'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 4ed59fb99f097a6352c2c12b7d8a1736993cf66960a677c52e0e3fc8b7370ea8 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "-1" not valid for option --width (-w): invalid digit found in string\n'
```

### width_empty_value

- cwd: ` <root> `
- argv: ` ['<ref>', '--width', '', 'grid10'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 10056ca098aada220ade26c7b7d754be5972c058a3ced5124d2a19a0c34a6822 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "" not valid for option --width (-w): cannot parse integer from empty string\n'
```

### width_empty_equals

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=', 'grid10'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` ef93281499d71ca4c191b24eb94c831d53a69c1dd14c790915064639d39482f5 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Unknown argument --width=\n'
```

### width_double_equals

- cwd: ` <root> `
- argv: ` ['<ref>', '--width==', 'grid10'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` e0440dffb49961b92678d4105fbdfa077f249c994b7b8adf76cb8ce713c36db5 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "=" not valid for option --width (-w): invalid digit found in string\n'
```

### width_too_large

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=18446744073709551616', 'grid10'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` d4bd7d6510ab9f7c956639210008ad5643d6b0059ed9683fba55e15f5c47be94 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "18446744073709551616" not valid for option --width (-w): number too large to fit in target type\n'
```

### width_plus

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=+', 'grid10'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 5a61a04c03c203ab35c688b98c301e6f209d462a9c62a042420630b455c4772a `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "+" not valid for option --width (-w): invalid digit found in string\n'
```

- note: ` Rust usize::from_str: "+" alone is InvalidDigit, not Empty `

### width_quote

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=a"b', 'grid10'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 77c6ce0c06c8ed180776495c2b867db0c9a23d4f929776e2a239cbbddd5aac03 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "a\\"b" not valid for option --width (-w): invalid digit found in string\n'
```

- note: ` FailedParse Debug-escapes the displayed value `

### width_nonascii_escape

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=a\xa0b', 'grid10'] `
- env extras: ` {} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 6fb202d3c05d0fc7fc6690b3df3504c69fb6b6c4df992912f4af9832716a2cb9 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "a\\u{a0}b" not valid for option --width (-w): invalid digit found in string\n'
```

- note: ` Rust Debug escapes non-ASCII non-printables as \u{a0}; the build copies them raw (documented divergence) `

### columns_plus

- cwd: ` <root> `
- argv: ` ['<ref>', 'grid10'] `
- env extras: ` {'COLUMNS': '+'} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 2e8c38d235695a7978c09826a99aa875b758418d44433e476fa2814ee1ef1349 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "+" not valid for environment variable COLUMNS: invalid digit found in string\n'
```

### columns_quote

- cwd: ` <root> `
- argv: ` ['<ref>', 'grid10'] `
- env extras: ` {'COLUMNS': 'a"b'} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` f42cbdab642918054b1de998327c05e696f6cd46e48b70f7a39376c0bd3f1c74 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "a\\"b" not valid for environment variable COLUMNS: invalid digit found in string\n'
```

### columns_control

- cwd: ` <root> `
- argv: ` ['<ref>', 'grid10'] `
- env extras: ` {'COLUMNS': 'a\tb'} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` d26b34c496af30ff49e1263badaae8caec759e8a1aaa27f93d5c9e2355e2dff6 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "a\\tb" not valid for environment variable COLUMNS: invalid digit found in string\n'
```

### columns_40_grid100

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'grid100'] `
- env extras: ` {'COLUMNS': '40'} `
- exit: ` 0 `
- stdout sha256: ` c392301c377d4aac3fe280f34faaaafb7dee701eefa8877083efcaca653f7ec6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g00  g13  g26  g39  g52  g65  g78  g91\ng01  g14  g27  g40  g53  g66  g79  g92\ng02  g15  g28  g41  g54  g67  g80  g93\ng03  g16  g29  g42  g55  g68  g81  g94\ng04  g17  g30  g43  g56  g69  g82  g95\ng05  g18  g31  g44  g57  g70  g83  g96\ng06  g19  g32  g45  g58  g71  g84  g97\ng07  g20  g33  g46  g59  g72  g85  g98\ng08  g21  g34  g47  g60  g73  g86  g99\ng09  g22  g35  g48  g61  g74  g87\ng10  g23  g36  g49  g62  g75  g88\ng11  g24  g37  g50  g63  g76  g89\ng12  g25  g38  g51  g64  g77  g90\n'
```

- stderr bytes:

```python
b''
```

### columns_0_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'mixed'] `
- env extras: ` {'COLUMNS': '0'} `
- exit: ` 0 `
- stdout sha256: ` 9261105d2889007c7d43d3dd79e86e8ccb1cfc3008101a4ea75eda3a47d5296f `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\n--x\nalpha\nBeta\nbroken\ndir\ndirlink\ngamma.txt\nlink\nnoexec\npipe\nprog\nsock\nspaced\nsub.dir\n'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` COLUMNS=0 is Set(0): a real grid, one column `

### columns_plus40

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'grid10'] `
- env extras: ` {'COLUMNS': '+40'} `
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

### columns_over_width

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=20', '--icons=never', 'grid10'] `
- env extras: ` {'COLUMNS': '80'} `
- exit: ` 0 `
- stdout sha256: ` 0bb1eb58e102f601407b113d9fe8652cbb99487705459236a4bc80467f3fe784 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g0  g2  g4  g6  g8\ng1  g3  g5  g7  g9\n'
```

- stderr bytes:

```python
b''
```

- note: ` --width wins over COLUMNS `

### columns_width_0

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=0', '--icons=never', 'grid10'] `
- env extras: ` {'COLUMNS': '40'} `
- exit: ` 0 `
- stdout sha256: ` 3022297e80515933a0629b51b56184b18b454da1966a6a4fbf795830ec3a56ea `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g0\ng1\ng2\ng3\ng4\ng5\ng6\ng7\ng8\ng9\n'
```

- stderr bytes:

```python
b''
```

- note: ` --width=0 still suppresses COLUMNS `

### columns_x

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'grid10'] `
- env extras: ` {'COLUMNS': 'x'} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 8388c1973ae2829a8fd6f0a3bff3a15449ae46be30570d88d12603f77997fb95 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "x" not valid for environment variable COLUMNS: invalid digit found in string\n'
```

### columns_empty

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'grid10'] `
- env extras: ` {'COLUMNS': ''} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 1bfc0779a096ccf8dbc6a79b76409588e5c28c6aa3203dc8e955d132ba29da37 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "" not valid for environment variable COLUMNS: cannot parse integer from empty string\n'
```

### columns_negative

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'grid10'] `
- env extras: ` {'COLUMNS': '-1'} `
- exit: ` 3 `
- stdout sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stderr sha256: ` 065faa785f9c212407c9a3e0551ba12bbb771f9227538f8b53e55b1c4da3c9b0 `
- stdout bytes:

```python
b''
```

- stderr bytes:

```python
b'eza: Value "-1" not valid for environment variable COLUMNS: invalid digit found in string\n'
```

### width_classify_auto

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '-F', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 800ad2c501f277062de4284a51c63f6f9a9d3a1cfe09490336e3dc19d1e71896 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-      broken@    link@   sock=\n--x    dir/       noexec  spaced@\nalpha  dirlink@   pipe|   sub.dir/\nBeta   gamma.txt  prog*   'tar get'\n"
```

- stderr bytes:

```python
b''
```

### width_classify_always

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '-F=always', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 800ad2c501f277062de4284a51c63f6f9a9d3a1cfe09490336e3dc19d1e71896 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-      broken@    link@   sock=\n--x    dir/       noexec  spaced@\nalpha  dirlink@   pipe|   sub.dir/\nBeta   gamma.txt  prog*   'tar get'\n"
```

- stderr bytes:

```python
b''
```

### width_classify_never

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '-F=never', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2fa2b08ec58c6a5250a3700aa3e0919e46a87af929797b63ef0d530f519afad4 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-      broken     link    sock\n--x    dir        noexec  spaced\nalpha  dirlink    pipe    sub.dir\nBeta   gamma.txt  prog    'tar get'\n"
```

- stderr bytes:

```python
b''
```

### G_classify_no_width

- cwd: ` <root> `
- argv: ` ['<ref>', '-G', '-F', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` d28b1351a88cb672b17fc44cbac79d5999790ef4f11199f83fc21a312590ec41 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-    alpha  broken  dirlink    link    pipe  sock    sub.dir\n--x  Beta   dir     gamma.txt  noexec  prog  spaced  'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` explicit -G without a width is not terminal-ish: no markers `

### columns_classify

- cwd: ` <root> `
- argv: ` ['<ref>', '-F', '--icons=never', 'mixed'] `
- env extras: ` {'COLUMNS': '40'} `
- exit: ` 0 `
- stdout sha256: ` 800ad2c501f277062de4284a51c63f6f9a9d3a1cfe09490336e3dc19d1e71896 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-      broken@    link@   sock=\n--x    dir/       noexec  spaced@\nalpha  dirlink@   pipe|   sub.dir/\nBeta   gamma.txt  prog*   'tar get'\n"
```

- stderr bytes:

```python
b''
```

### columns_colour_auto

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=auto', '--icons=never', 'mixed'] `
- env extras: ` {'COLUMNS': '40'} `
- exit: ` 0 `
- stdout sha256: ` 2fa2b08ec58c6a5250a3700aa3e0919e46a87af929797b63ef0d530f519afad4 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-      broken     link    sock\n--x    dir        noexec  spaced\nalpha  dirlink    pipe    sub.dir\nBeta   gamma.txt  prog    'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` Set width grids but automatic colour still needs a real stdout TTY `

### width_colour_auto

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '--color=auto', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` d28b1351a88cb672b17fc44cbac79d5999790ef4f11199f83fc21a312590ec41 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-    alpha  broken  dirlink    link    pipe  sock    sub.dir\n--x  Beta   dir     gamma.txt  noexec  prog  spaced  'tar get'\n"
```

- stderr bytes:

```python
b''
```

### width_colour_always

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '--color=always', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 903339cfa1c7bcebdc388b9bcd6e5308e02b66f11f515b5691a2babde9157fc3 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-    alpha  \x1b[31mbroken\x1b[0m  \x1b[36mdirlink\x1b[0m    \x1b[36mlink\x1b[0m    \x1b[33mpipe\x1b[0m  \x1b[1;31msock\x1b[0m    \x1b[1;34msub.dir\x1b[0m\n--x  Beta   \x1b[1;34mdir\x1b[0m     gamma.txt  noexec  \x1b[1;32mprog\x1b[0m  \x1b[36mspaced\x1b[0m  'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` broken links are red, links cyan; no targets `

### width_colour_F

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '--color=always', '-F=always', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 4f97ac9673a7186b1330e65d1a90755fe5156bf590ff61516e83d764ba0ec689 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-    alpha  \x1b[31mbroken\x1b[0m@  \x1b[36mdirlink\x1b[0m@   \x1b[36mlink\x1b[0m@   \x1b[33mpipe\x1b[0m|  \x1b[1;31msock\x1b[0m=    \x1b[1;34msub.dir\x1b[0m/\n--x  Beta   \x1b[1;34mdir\x1b[0m/     gamma.txt  noexec  \x1b[1;32mprog\x1b[0m*  \x1b[36mspaced\x1b[0m@  'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ` the marker is a plain bit on the styled link name `

### width_colour_links

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '--color=always', '--icons=never', 'links'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 230a380273e7fd117783ba5f6097044e2b2f19b43f320881ae239f63999c4fa8 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"\x1b[36mb\x1b[0m         \x1b[36mexec\x1b[0m         \x1b[31mloop1\x1b[0m  \x1b[1;34mrealdir\x1b[0m      \x1b[36mspaced\x1b[0m\n\x1b[36mchain\x1b[0m     \x1b[1;32mexec-target\x1b[0m  \x1b[31mloop2\x1b[0m  \x1b[36mrealdirlink\x1b[0m  'tar get'\n\x1b[31mdangling\x1b[0m  file         \x1b[36mreal\x1b[0m   \x1b[36mrealfile\x1b[0m     \x1b[36mto_realdirlink\x1b[0m\n"
```

- stderr bytes:

```python
b''
```

### width_colour_aa

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '--color=always', '-aa', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f882164cb5591f6434fe90a9ace7cb516f70cbc15cad69007b4581e03d2ef207 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-         .hidden  \x1b[36mdirlink\x1b[0m    \x1b[1;32mprog\x1b[0m\n--x       alpha    gamma.txt  \x1b[1;31msock\x1b[0m\n\x1b[1;34m.\x1b[0m         Beta     \x1b[36mlink\x1b[0m       \x1b[36mspaced\x1b[0m\n\x1b[1;34m..\x1b[0m        \x1b[31mbroken\x1b[0m   noexec     \x1b[1;34msub.dir\x1b[0m\n..double  \x1b[1;34mdir\x1b[0m      \x1b[33mpipe\x1b[0m       'tar get'\n"
```

- stderr bytes:

```python
b''
```

### width_aa_F

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '-aa', '-F', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f74efc637327060848e90865f10b2d14933645c40ad43539f31646d3060b8bfd `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-         .hidden  dirlink@   prog*\n--x       alpha    gamma.txt  sock=\n./        Beta     link@      spaced@\n../       broken@  noexec     sub.dir/\n..double  dir/     pipe|      'tar get'\n"
```

- stderr bytes:

```python
b''
```

- note: ``synthetic `.`/`..` are grid cells and take the / marker``

### width_no_quotes

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '--no-quotes', '--icons=never', 'quoting'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 500cc9e0da73d6023b6b7d18be4e57c9b140641ac2652fc13245c7e400e9af28 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'               del\\u{7f}name\na b            dquote"name\napos\'trophe    esc\\u{1b}name\nback\\slash     lead space\nboth \' and "   nl\\nname\nbrokenrel      plain\nbroken spaced  tab\\tname\ncr\\rname       trail \nctl\\u{1}name   valid spaced\nctl target     with space\n'
```

- stderr bytes:

```python
b''
```

### width_quoting_F

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '-F=always', '--icons=never', 'quoting'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` c6f3a9ec0858469a60833bc9c14add595c4424920ca2334ca0525dfd6922df2c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\' \'               del\\u{7f}name\n\'a b\'/            dquote"name\n"apos\'trophe"     esc\\u{1b}name\nback\\slash        \'lead space\'\n"both \' and ""    nl\\nname\nbrokenrel@        plain\n\'broken spaced\'@  tab\\tname\ncr\\rname          \'trail \'\nctl\\u{1}name      \'valid spaced\'@\n\'ctl target\'@     \'with space\'\n'
```

- stderr bytes:

```python
b''
```

### width_d_dir_F

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '-d', '-F=always', '--icons=never', 'mixed/dir'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` bab99c37ad7102c59182c6e3dc8e0ab938b743ff5acf82a3f9566baedf3413cc `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'mixed/dir/\n'
```

- stderr bytes:

```python
b''
```

### width_dangling

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '--icons=never', 'links/dangling'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 1dcf565d12db6262ed850feaa6ac2ae7a11dde4f19afde1ace73f6edd71244a0 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'links/dangling\n'
```

- stderr bytes:

```python
b''
```

- note: ` a single symlink operand grids its name only `

### width_dangling_F

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '-F=always', '--icons=never', 'links/dangling'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 44c80a33207d3c24efff82f99e5b194b525c94a61dbe1e8e8066c027122b43ca `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'links/dangling@\n'
```

- stderr bytes:

```python
b''
```

### width_dangling_colour

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '--color=always', '--icons=never', 'links/dangling'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 71f0e6de18965c87bc69fb331fae73f46b63a9446232e1aca23a3fd2e18eadc1 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\x1b[36mlinks/\x1b[31mdangling\x1b[0m\n'
```

- stderr bytes:

```python
b''
```

### width_dirlink_d_F

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=80', '-d', '-F=always', '--icons=never', 'links/realdirlink'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 342f6dbcb90ed6cd5f557670f1f4a23f44f57f82cbacacdf2eca8b4333c9dde1 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'links/realdirlink@\n'
```

- stderr bytes:

```python
b''
```

### pty_grid_80

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'grid10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 4496681e68eb0d4c32a8890b2b26a60923043b8ac6c082dfda8b9dae7697d8c0 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g0  g1  g2  g3  g4  g5  g6  g7  g8  g9\r\n'
```

- stderr bytes:

```python
b''
```

### pty_grid_40

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'grid10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 4496681e68eb0d4c32a8890b2b26a60923043b8ac6c082dfda8b9dae7697d8c0 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g0  g1  g2  g3  g4  g5  g6  g7  g8  g9\r\n'
```

- stderr bytes:

```python
b''
```

### pty_grid_20

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'grid10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 63affa6f18b7290d528a6da73d825ae20bc6487409c65fac9289f28e36b0564c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g0  g2  g4  g6  g8\r\ng1  g3  g5  g7  g9\r\n'
```

- stderr bytes:

```python
b''
```

### pty_zero_cols

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 8268f20970b5f59dccd1e1767fe51b6371ad5941cfe3064ad1284e4a3d784399 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\r\n--x\r\nalpha\r\nBeta\r\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\r\n\x1b[1;34mdir\x1b[0m\r\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\r\ngamma.txt\r\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\r\nnoexec\r\n\x1b[33mpipe\x1b[0m\r\n\x1b[1;32mprog\x1b[0m\r\n\x1b[1;31msock\x1b[0m\r\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\r\n\x1b[1;34msub.dir\x1b[0m\r\n'tar get'\r\n"
```

- stderr bytes:

```python
b''
```

- note: ` a zero column report is not terminal-ish: Lines mode `

### pty_zero_rows

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 8268f20970b5f59dccd1e1767fe51b6371ad5941cfe3064ad1284e4a3d784399 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\r\n--x\r\nalpha\r\nBeta\r\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\r\n\x1b[1;34mdir\x1b[0m\r\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\r\ngamma.txt\r\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\r\nnoexec\r\n\x1b[33mpipe\x1b[0m\r\n\x1b[1;32mprog\x1b[0m\r\n\x1b[1;31msock\x1b[0m\r\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\r\n\x1b[1;34msub.dir\x1b[0m\r\n'tar get'\r\n"
```

- stderr bytes:

```python
b''
```

- note: ` rows=0 cols=80: terminal_size is None, so the reference stays in Lines mode (colour auto on, no automatic classify). io::termWidth exposes only the column count, so this build sees cols=80, grids by default and enables auto classify (0.5-runtime limitation, documented) `

### pty_zero_rows_F

- cwd: ` <root> `
- argv: ` ['<ref>', '-G', '-F', '--icons=never', 'mixed'] `
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

- note: ` rows=0: explicit -G grids, but is_a_tty is false so the reference adds no auto-classify markers; this build sees cols=80 and adds them (documented) `

### pty_zero_rows_lines_F

- cwd: ` <root> `
- argv: ` ['<ref>', '-1', '-F', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 8268f20970b5f59dccd1e1767fe51b6371ad5941cfe3064ad1284e4a3d784399 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-\r\n--x\r\nalpha\r\nBeta\r\n\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\r\n\x1b[1;34mdir\x1b[0m\r\n\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\r\ngamma.txt\r\n\x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\r\nnoexec\r\n\x1b[33mpipe\x1b[0m\r\n\x1b[1;32mprog\x1b[0m\r\n\x1b[1;31msock\x1b[0m\r\n\x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\r\n\x1b[1;34msub.dir\x1b[0m\r\n'tar get'\r\n"
```

- stderr bytes:

```python
b''
```

- note: ` rows=0: the reference Lines view has no automatic markers; this build adds them (documented) `

### pty_zero_rows_tree_F

- cwd: ` <root> `
- argv: ` ['<ref>', '-T', '-F', '--icons=never', 'tree'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 2b6bf5aa8777add551e1919bd51595e5bcda006ba33bbfc205943e8a99df72f6 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\x1b[1;34mtree\x1b[0m\r\n\x1b[1;90m\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \x1b[0ma.txt\r\n\x1b[1;90m\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \x1b[0m\x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\r\n\x1b[1;90m\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \x1b[34md1\x1b[0m\r\n\x1b[1;90m\xe2\x94\x82   \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \x1b[0mb.txt\r\n\x1b[1;90m\xe2\x94\x82   \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 \x1b[34md2\x1b[0m\r\n\x1b[1;90m\xe2\x94\x82       \xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \x1b[0mc.txt\r\n\x1b[1;90m\xe2\x94\x82       \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 \x1b[34md3\x1b[0m\r\n\x1b[1;90m\xe2\x94\x82           \xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 \x1b[0md.txt\r\n\x1b[1;90m\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \x1b[0m\x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34md1\x1b[0m\r\n\x1b[1;90m\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 \x1b[34memptydir\x1b[0m\r\n\x1b[1;90m\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 \x1b[34mhidden\x1b[0m\r\n'
```

- stderr bytes:

```python
b''
```

- note: ` rows=0: the reference tree has no automatic markers; this build adds them (documented) `

### pty_zero_rows_long_F

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', '-F', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 67094e6dc2856e3b0330906f47b6da3c71ef6ac66478903e2ccc181523790717 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b".\x1b[1;33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[32m5\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m -\r\n.\x1b[1;33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[32m9\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m --x\r\n.\x1b[1;33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[32m6\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m alpha\r\n.\x1b[1;33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[32m5\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m Beta\r\n\x1b[36ml\x1b[1;33mr\x1b[31mw\x1b[32mx\x1b[0m\x1b[33mr\x1b[31mw\x1b[32mx\x1b[33mr\x1b[31mw\x1b[32mx\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m \x1b[36mbroken\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mmissing\x1b[0m\r\n\x1b[1;34md\x1b[33mr\x1b[31mw\x1b[32mx\x1b[0m\x1b[33mr\x1b[1;90m-\x1b[0m\x1b[32mx\x1b[33mr\x1b[1;90m-\x1b[0m\x1b[32mx\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m \x1b[1;34mdir\x1b[0m\r\n\x1b[36ml\x1b[1;33mr\x1b[31mw\x1b[32mx\x1b[0m\x1b[33mr\x1b[31mw\x1b[32mx\x1b[33mr\x1b[31mw\x1b[32mx\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m \x1b[36mdirlink\x1b[0m \x1b[1;90m->\x1b[0m \x1b[1;34mdir\x1b[0m\r\n.\x1b[1;33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[32m6\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m gamma.txt\r\n\x1b[36ml\x1b[1;33mr\x1b[31mw\x1b[32mx\x1b[0m\x1b[33mr\x1b[31mw\x1b[32mx\x1b[33mr\x1b[31mw\x1b[32mx\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m \x1b[36mlink\x1b[0m \x1b[1;90m->\x1b[0m alpha\r\n.\x1b[1;33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[32m3\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m noexec\r\n\x1b[33m|\x1b[1mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m \x1b[33mpipe\x1b[0m\r\n.\x1b[1;33mr\x1b[31mw\x1b[4;32mx\x1b[0m\x1b[33mr\x1b[1;90m-\x1b[0m\x1b[32mx\x1b[33mr\x1b[1;90m-\x1b[0m\x1b[32mx\x1b[0m \x1b[32m17\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m \x1b[1;32mprog\x1b[0m\r\n\x1b[1;31ms\x1b[33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m \x1b[1;31msock\x1b[0m\r\n\x1b[36ml\x1b[1;33mr\x1b[31mw\x1b[32mx\x1b[0m\x1b[33mr\x1b[31mw\x1b[32mx\x1b[33mr\x1b[31mw\x1b[32mx\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m \x1b[36mspaced\x1b[0m \x1b[1;90m->\x1b[0m 'tar get'\r\n\x1b[1;34md\x1b[33mr\x1b[31mw\x1b[32mx\x1b[0m\x1b[33mr\x1b[1;90m-\x1b[0m\x1b[32mx\x1b[33mr\x1b[1;90m-\x1b[0m\x1b[32mx\x1b[0m  \x1b[1;90m-\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m \x1b[1;34msub.dir\x1b[0m\r\n.\x1b[1;33mr\x1b[31mw\x1b[90m-\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m\x1b[33mr\x1b[1;90m--\x1b[0m  \x1b[32m7\x1b[0m \x1b[34m2020-09-13 12:26\x1b[0m 'tar get'\r\n"
```

- stderr bytes:

```python
b''
```

- note: ` rows=0: the reference restricted long view has no automatic markers; this build adds them (documented) `

### pty_colour_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'mixed'] `
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

- note: ` auto colour and grid on a real terminal `

### pty_F_mixed

- cwd: ` <root> `
- argv: ` ['<ref>', '-F', '--icons=never', 'mixed'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` a0d5711540d4ee89c9f6fefa969fff4e7d6ad1ffdebb39c4ff915b6071364beb `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"-    alpha  \x1b[31mbroken\x1b[0m@  \x1b[36mdirlink\x1b[0m@   \x1b[36mlink\x1b[0m@   \x1b[33mpipe\x1b[0m|  \x1b[1;31msock\x1b[0m=    \x1b[1;34msub.dir\x1b[0m/\r\n--x  Beta   \x1b[1;34mdir\x1b[0m/     gamma.txt  noexec  \x1b[1;32mprog\x1b[0m*  \x1b[36mspaced\x1b[0m@  'tar get'\r\n"
```

- stderr bytes:

```python
b''
```

### pty_columns_20

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'grid10'] `
- env extras: ` {'COLUMNS': '20'} `
- exit: ` 0 `
- stdout sha256: ` 63affa6f18b7290d528a6da73d825ae20bc6487409c65fac9289f28e36b0564c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g0  g2  g4  g6  g8\r\ng1  g3  g5  g7  g9\r\n'
```

- stderr bytes:

```python
b''
```

### pty_width_40

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '--icons=never', 'grid10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 4496681e68eb0d4c32a8890b2b26a60923043b8ac6c082dfda8b9dae7697d8c0 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g0  g1  g2  g3  g4  g5  g6  g7  g8  g9\r\n'
```

- stderr bytes:

```python
b''
```

### pty_width_0

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=0', '--icons=never', 'grid10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 4496681e68eb0d4c32a8890b2b26a60923043b8ac6c082dfda8b9dae7697d8c0 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g0  g1  g2  g3  g4  g5  g6  g7  g8  g9\r\n'
```

- stderr bytes:

```python
b''
```

### pty_oneline

- cwd: ` <root> `
- argv: ` ['<ref>', '-1', '--icons=never', 'grid10'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 0cffd833c6dc103f227f403b8479f8ce9f7559871efb7ff5d228862da333007d `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'g0\r\ng1\r\ng2\r\ng3\r\ng4\r\ng5\r\ng6\r\ng7\r\ng8\r\ng9\r\n'
```

- stderr bytes:

```python
b''
```

### utf8_default

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'utf8'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 9f56b40a726b76c57556b10e2a6f89e783c5e855d4ff331f4dd65ba53fef345c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a\ncomb-link -> e\xcc\x81\ne\xcc\x81\nf\n\xc3\xa9\n\xc3\xa9-link -> \xc3\xa9\n\xce\xa9\n\xe4\xb8\xad\n\xef\xbd\xb1\n\xf0\x9f\x98\x80\n'
```

- stderr bytes:

```python
b''
```

- note: ` code point order: a, e+U+0301, f, e-acute, omega, CJK, halfwidth katakana, emoji `

### utf8_Name

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=Name', '--icons=never', 'utf8'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 9f56b40a726b76c57556b10e2a6f89e783c5e855d4ff331f4dd65ba53fef345c `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a\ncomb-link -> e\xcc\x81\ne\xcc\x81\nf\n\xc3\xa9\n\xc3\xa9-link -> \xc3\xa9\n\xce\xa9\n\xe4\xb8\xad\n\xef\xbd\xb1\n\xf0\x9f\x98\x80\n'
```

- stderr bytes:

```python
b''
```

### utf8_grid_10

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=10', '--icons=never', 'utf8'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e35a5ff70d07760d7768efef8d36db26f1b313f6b7ce7047270fdcc475e8e0f7 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a\ncomb-link\ne\xcc\x81\nf\n\xc3\xa9\n\xc3\xa9-link\n\xce\xa9\n\xe4\xb8\xad\n\xef\xbd\xb1\n\xf0\x9f\x98\x80\n'
```

- stderr bytes:

```python
b''
```

- note: ` e-acute width 1, CJK/emoji width 2, combining sequence width 1 `

### utf8_grid_12

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=12', '--icons=never', 'utf8'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` e35a5ff70d07760d7768efef8d36db26f1b313f6b7ce7047270fdcc475e8e0f7 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a\ncomb-link\ne\xcc\x81\nf\n\xc3\xa9\n\xc3\xa9-link\n\xce\xa9\n\xe4\xb8\xad\n\xef\xbd\xb1\n\xf0\x9f\x98\x80\n'
```

- stderr bytes:

```python
b''
```

### utf8_grid_20

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=20', '--icons=never', 'utf8'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` a74528ded65ede1e7a1f1bb7ed7827f9c5c74c64d3c2b165584ce6f2e1028d1e `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a          \xc3\xa9-link\ncomb-link  \xce\xa9\ne\xcc\x81          \xe4\xb8\xad\nf          \xef\xbd\xb1\n\xc3\xa9          \xf0\x9f\x98\x80\n'
```

- stderr bytes:

```python
b''
```

### utf8_grid_40

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '--icons=never', 'utf8'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 4961ac394c741e81c298b43dd3f544ff3a9560803abde96cb5dd9c9f559bd170 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a          e\xcc\x81  \xc3\xa9       \xce\xa9   \xef\xbd\xb1\ncomb-link  f  \xc3\xa9-link  \xe4\xb8\xad  \xf0\x9f\x98\x80\n'
```

- stderr bytes:

```python
b''
```

### utf8_columns_12

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'utf8'] `
- env extras: ` {'COLUMNS': '12'} `
- exit: ` 0 `
- stdout sha256: ` e35a5ff70d07760d7768efef8d36db26f1b313f6b7ce7047270fdcc475e8e0f7 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a\ncomb-link\ne\xcc\x81\nf\n\xc3\xa9\n\xc3\xa9-link\n\xce\xa9\n\xe4\xb8\xad\n\xef\xbd\xb1\n\xf0\x9f\x98\x80\n'
```

- stderr bytes:

```python
b''
```

### utf8_fold_default

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'utf8fold'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 1cda0560b9ef77e3bfbe158daa683af53185894393c3f3fe69e22a90022b5d9b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\xcf\x82\n\xce\xa3\n\xcf\x83\n\xd0\xb6\n\xd0\x96\n\xef\xbc\xa1\n\xef\xbd\x81\n'
```

- stderr bytes:

```python
b''
```

- note: ` sigma/Cyrillic/fullwidth lowercasing; tie order is readdir `

### utf8_fold_Name

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=Name', '--icons=never', 'utf8fold'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` f9545a2b1109ab9ab4b5654493d25c21ed126774fe3dc0e07732bfecf17dbcfe `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'\xce\xa3\n\xcf\x82\n\xcf\x83\n\xd0\x96\n\xd0\xb6\n\xef\xbc\xa1\n\xef\xbd\x81\n'
```

- stderr bytes:

```python
b''
```

### utf8_tie_default

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'utf8tie'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 8d09c4ca783deda3a39e3b23368a569498647590b95244a4890b5b6ae87fb3a0 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a\nA\n\xc3\x80\n\xc3\xa0\n\xc3\xa9\n\xc3\x89\n'
```

- stderr bytes:

```python
b''
```

- note: ` E-acute/e-acute fold tie: readdir order `

### utf8_tie_Name

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=Name', '--icons=never', 'utf8tie'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 195c8c97a67cc4d2a222e503d09d7e6155aaa00b780bf76fb64f42ec6e644431 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'A\na\n\xc3\x80\n\xc3\x89\n\xc3\xa0\n\xc3\xa9\n'
```

- stderr bytes:

```python
b''
```

### utf8_ws_default

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'utf8ws'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 3c6b0aab11243ce9628fa0563667df1d1e0db046e5a1f95f6bc3532e4d71f3d8 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"a\xe2\x80\x83b\na\xe3\x80\x80b\nab\na\xc2\xa0b\n'a b'\n"
```

- stderr bytes:

```python
b''
```

- note: ` ASCII space, NBSP, EM SPACE and IDEOGRAPHIC SPACE all compare Equal: readdir order `

### bad_list

- cwd: ` <root> `
- argv: ` ['<ref>', '--icons=never', 'utf8bad'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 9a8ff88069111dd55e69ed43ad37ef204aed5028c1ae04b5a92dd3b476433892 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"a-\xef\xbf\xbd\nb-\xef\xbf\xbd\nc-\xef\xbf\xbd(\nd-\xef\xbf\xbd\ne-\xef\xbf\xbd\nf-\xef\xbf\xbd\xef\xbf\xbd\ng-\xc3\xa9\nh-link -> t-\xef\xbf\xbd\ni-link -> 'sp-\xef\xbf\xbd ace'\nj-link -> sub-\xef\xbf\xbd/f\nk-link -> g-\xc3\xa9\nsub-\xef\xbf\xbd\n"
```

- stderr bytes:

```python
b''
```

- note: ` one U+FFFD per maximal invalid subpart `

### bad_Name

- cwd: ` <root> `
- argv: ` ['<ref>', '--sort=Name', '--icons=never', 'utf8bad'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 9a8ff88069111dd55e69ed43ad37ef204aed5028c1ae04b5a92dd3b476433892 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"a-\xef\xbf\xbd\nb-\xef\xbf\xbd\nc-\xef\xbf\xbd(\nd-\xef\xbf\xbd\ne-\xef\xbf\xbd\nf-\xef\xbf\xbd\xef\xbf\xbd\ng-\xc3\xa9\nh-link -> t-\xef\xbf\xbd\ni-link -> 'sp-\xef\xbf\xbd ace'\nj-link -> sub-\xef\xbf\xbd/f\nk-link -> g-\xc3\xa9\nsub-\xef\xbf\xbd\n"
```

- stderr bytes:

```python
b''
```

### bad_long

- cwd: ` <root> `
- argv: ` ['<ref>', '-l', '--no-user', '--bytes', '--time-style=long-iso', '--icons=never', 'utf8bad'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 28f92c698cd119496388baa11ac75000db1f9557f6c5be31ac9bb114ea5f349a `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b".rw-r--r-- 2 2020-09-13 12:26 a-\xef\xbf\xbd\n.rw-r--r-- 2 2020-09-13 12:26 b-\xef\xbf\xbd\n.rw-r--r-- 2 2020-09-13 12:26 c-\xef\xbf\xbd(\n.rw-r--r-- 2 2020-09-13 12:26 d-\xef\xbf\xbd\n.rw-r--r-- 2 2020-09-13 12:26 e-\xef\xbf\xbd\n.rw-r--r-- 2 2020-09-13 12:26 f-\xef\xbf\xbd\xef\xbf\xbd\n.rw-r--r-- 2 2020-09-13 12:26 g-\xc3\xa9\nlrwxrwxrwx - 2020-09-13 12:26 h-link -> t-\xef\xbf\xbd\nlrwxrwxrwx - 2020-09-13 12:26 i-link -> 'sp-\xef\xbf\xbd ace'\nlrwxrwxrwx - 2020-09-13 12:26 j-link -> sub-\xef\xbf\xbd/f\nlrwxrwxrwx - 2020-09-13 12:26 k-link -> g-\xc3\xa9\ndrwxr-xr-x - 2020-09-13 12:26 sub-\xef\xbf\xbd\n"
```

- stderr bytes:

```python
b''
```

- note: ` lossy names and lossy symlink targets `

### bad_grid_20

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=20', '--icons=never', 'utf8bad'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 77522f20e21e9316e815f74e0347703fb0ad760183e060557271a7ef40b3df27 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a-\xef\xbf\xbd   e-\xef\xbf\xbd     i-link\nb-\xef\xbf\xbd   f-\xef\xbf\xbd\xef\xbf\xbd    j-link\nc-\xef\xbf\xbd(  g-\xc3\xa9     k-link\nd-\xef\xbf\xbd   h-link  sub-\xef\xbf\xbd\n'
```

- stderr bytes:

```python
b''
```

- note: ` grid width counts replacements as width 1 `

### bad_grid_40

- cwd: ` <root> `
- argv: ` ['<ref>', '--width=40', '--icons=never', 'utf8bad'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 008bea9f3dd6178fbeae64f117980434490549ce056f2160be12eeea59a429d9 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b'a-\xef\xbf\xbd  c-\xef\xbf\xbd(  e-\xef\xbf\xbd   g-\xc3\xa9     i-link  k-link\nb-\xef\xbf\xbd  d-\xef\xbf\xbd   f-\xef\xbf\xbd\xef\xbf\xbd  h-link  j-link  sub-\xef\xbf\xbd\n'
```

- stderr bytes:

```python
b''
```

### bad_colour

- cwd: ` <root> `
- argv: ` ['<ref>', '--color=always', '--icons=never', 'utf8bad'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 4ea081b2abc2e9d7c24ebea5bb96c6d16c8cdef13883ad058e3e440e9ecaf31b `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"a-\xef\xbf\xbd\nb-\xef\xbf\xbd\nc-\xef\xbf\xbd(\nd-\xef\xbf\xbd\ne-\xef\xbf\xbd\nf-\xef\xbf\xbd\xef\xbf\xbd\ng-\xc3\xa9\n\x1b[36mh-link\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31mt-\xef\xbf\xbd\x1b[0m\n\x1b[36mi-link\x1b[0m \x1b[31m->\x1b[0m \x1b[4;31m'sp-\xef\xbf\xbd ace'\x1b[0m\n\x1b[36mj-link\x1b[0m \x1b[1;90m->\x1b[0m \x1b[36msub-\xef\xbf\xbd/\x1b[0mf\n\x1b[36mk-link\x1b[0m \x1b[1;90m->\x1b[0m g-\xc3\xa9\n\x1b[1;34msub-\xef\xbf\xbd\x1b[0m\n"
```

- stderr bytes:

```python
b''
```

### bad_F

- cwd: ` <root> `
- argv: ` ['<ref>', '-F=always', '--icons=never', 'utf8bad'] `
- env extras: ` {} `
- exit: ` 0 `
- stdout sha256: ` 70b025491e39b5cb03342a920880ebe9a5a2b5e8e06c802303131699c99784b5 `
- stderr sha256: ` e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 `
- stdout bytes:

```python
b"a-\xef\xbf\xbd\nb-\xef\xbf\xbd\nc-\xef\xbf\xbd(\nd-\xef\xbf\xbd\ne-\xef\xbf\xbd\nf-\xef\xbf\xbd\xef\xbf\xbd\ng-\xc3\xa9\nh-link -> t-\xef\xbf\xbd\ni-link -> 'sp-\xef\xbf\xbd ace'\nj-link -> sub-\xef\xbf\xbd/f\nk-link -> g-\xc3\xa9\nsub-\xef\xbf\xbd/\n"
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
- Invalid-UTF-8 display is now covered by exact E2 goldens (the `utf8`
  family) whenever the fixture can create invalid names; the P0 `utf8` probes
  remain raw-shape evidence. There is still no shape-comparison runner: that
  means there is no shape oracle, not that lossy display is untested.
- REFERENCE.md is not byte-for-byte reproducible: the host manifest records
  live host data (e.g. `/proc` entry counts). That is accepted.

- A rows=0 terminal is invisible to `io::termWidth`, which exposes only the
  column count. The reference's `terminal_size` requires non-zero rows and
  columns, so with rows=0 it is not terminal-ish: the default view is Lines,
  explicit `-G` still grids, and automatic classify markers stay off in every
  view. This build sees the columns and treats the run as terminal-ish (grid
  by default and auto markers). The `pty_zero_rows*` probes record the
  reference bytes; this is an accepted 0.5-runtime limitation and no
  differential case sets rows=0.
- Rust's `{:?}` Debug escaping of an option value also escapes non-ASCII
  non-printables (`\u{a0}`, combining marks, format controls, U+0085,
  U+2028, U+FEFF) as `\u{...}`; `debugText` copies such bytes raw. ASCII
  quote, backslash, NUL, tab/newline/CR, other C0 and DEL are escaped
  identically. `width_nonascii_escape` records the reference; the divergence
  is documented.
- `errors::debugPath` prints an operand's raw bytes in a missing-operand
  error; the reference's `{path:?}` Debug-escapes invalid UTF-8 and controls.
  This is an inherited P2 limitation outside P6b's files.

Note for reviewers: `git diff --check` flags trailing whitespace in
`long/dir_mode000.stderr` (the permission summary line ends with a space) and
in `grid/quoting_noquotes_width.stdout` line 2 (`trail ` is a NoQuotes cell
that keeps its trailing space). Both are exact reference bytes, not
formatting; do not edit them.
