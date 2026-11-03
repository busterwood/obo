#!/usr/bin/env python3
"""Writes the regex benchmark's corpus: about 3 MB of text, the same every time (a fixed seed),
so no data file is committed.

    python3 obx/bench/regex/gen_corpus.py [OUT]     (default: corpus.txt here)

Three parts, as design/regex.md section 4 asks: English-like prose (sentences of common words
with the names the patterns look for, now and then an email address or a date), a log file
(timestamped lines, some of them `ERROR ... timeout`), and source code (C-like functions). ASCII
only, so engines that differ on Unicode (\\w is ASCII in Go's syntax, Unicode in Rust's) count
the same matches.
"""
import os, random, sys

WORDS = '''the of and to a in that it was he his i for with as had you not be her on at by which
have or from this him but all she they were my are me one their so an said them we who would been
will no when there if more out up into do any your what has man could other than our some very
time upon about may its only now like little then can should made did us such great before must
two these see know over much down after first mr good men own never most old shall day where
those came come himself way work life without go make well through being long say might how am
too even under new just think again back here also little room door house window street night
morning hand eyes face head friend letter case matter fact point course moment word thing place
light paper evening question reason answer clear strange singular remarkable quite indeed perhaps
however certainly already nothing something everything anything nobody'''.split()
NAMES = ['Sherlock Holmes', 'Holmes', 'Watson', 'Dr. Watson', 'Irene Adler', 'Lestrade', 'Mrs. Hudson',
         'Sherlock', 'Mycroft', 'Moriarty', 'Baker Street', 'SHERLOCK HOLMES', 'sherlock']
DOMAINS = ['example.com', 'baker-street.co.uk', 'mail.example.org', 'yard.police.uk', 'post.net']
LEVELS = ['INFO'] * 12 + ['DEBUG'] * 6 + ['WARN'] * 2 + ['ERROR']
ERRORS = ['connection timeout', 'read timeout', 'connection refused', 'upstream timeout', 'disk full',
          'request timeout after 30s', 'timeout', 'bad gateway']
IDENTS = ['buf', 'len', 'count', 'node', 'next', 'value', 'key', 'table', 'index', 'result', 'err', 'ctx',
          'start', 'end', 'size', 'flags', 'state', 'item', 'list', 'map']


def prose(r, nbytes):
    out = []
    n = 0
    while n < nbytes:
        words = []
        for _ in range(r.randint(6, 24)):
            k = r.random()
            if k < 0.03:
                words.append(r.choice(NAMES))
            elif k < 0.032:
                user = r.choice(['j.watson', 'sherlock', 'irene.adler', 'm.holmes', 'lestrade', 'hudson+lodgings'])
                words.append('%s@%s' % (user, r.choice(DOMAINS)))
            elif k < 0.036:
                words.append('%04d-%02d-%02d' % (r.randint(1880, 2026), r.randint(1, 12), r.randint(1, 28)))
            else:
                # The first words more often.
                words.append(WORDS[int(r.expovariate(1 / 50)) % len(WORDS)])
        s = ' '.join(words)
        s = s[0].upper() + s[1:] + r.choice(['.', '.', '.', '?', '!', ';', ','])
        out.append(s)
        n += len(s) + 1
        if r.random() < 0.15:
            out.append('\n')
    # Paragraphs wrapped at 76 columns, between words.
    lines = []
    for para in ' '.join(out).split('\n'):
        line = ''
        for w in para.split():
            if line and len(line) + 1 + len(w) > 76:
                lines.append(line)
                line = w
            else:
                line = line + ' ' + w if line else w
        lines.append(line)
        lines.append('')
    return '\n'.join(lines) + '\n'


def log(r, nbytes):
    out = []
    n = 0
    t = 0
    while n < nbytes:
        t += r.randint(1, 900)
        lvl = r.choice(LEVELS)
        stamp = '2026-11-%02dT%02d:%02d:%02d.%03dZ' % (1 + t // 86400000 % 28, t // 3600000 % 24, t // 60000 % 60,
                                                     t // 1000 % 60, t % 1000)
        if lvl == 'ERROR':
            line = 'ERROR %s worker=%d %s' % (stamp, r.randint(1, 64), r.choice(ERRORS))
        else:
            line = '%s %s worker=%d request id=%08x path=/api/v1/%s took %dms' % (
                lvl, stamp, r.randint(1, 64), r.getrandbits(32), r.choice(IDENTS), r.randint(1, 4000))
        out.append(line)
        n += len(line) + 1
    return '\n'.join(out) + '\n'


def source(r, nbytes):
    out = []
    n = 0
    while n < nbytes:
        name = r.choice(IDENTS) + '_' + r.choice(IDENTS)
        lines = ['static int %s(struct %s *%s, size_t %s) {' % (name, r.choice(IDENTS), r.choice(IDENTS), r.choice(IDENTS))]
        for _ in range(r.randint(3, 15)):
            a, b, c = r.choice(IDENTS), r.choice(IDENTS), r.choice(IDENTS)
            lines.append(r.choice([
                '    if (%s->%s == NULL) return -1;' % (a, b),
                '    %s = %s + %d;' % (a, b, r.randint(0, 999)),
                '    for (size_t i = 0; i < %s; i++) %s[i] = %s;' % (a, b, c),
                '    /* %s: %s %s */' % (a, ' '.join(r.choice(WORDS[:80]) for _ in range(5)), c),
                '    %s(%s, "%s");' % (a, b, r.choice(WORDS)),
            ]))
        lines.append('}')
        s = '\n'.join(lines) + '\n'
        out.append(s)
        n += len(s)
    return ''.join(out)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'corpus.txt')
    r = random.Random(20261103)
    text = prose(r, 2000000) + log(r, 800000) + source(r, 400000)
    with open(path, 'w') as f:
        f.write(text)
    print('wrote %s: %d bytes' % (path, len(text)))


if __name__ == '__main__':
    main()
