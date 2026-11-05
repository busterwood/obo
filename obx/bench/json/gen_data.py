#!/usr/bin/env python3
"""Writes the JSON benchmark's input: about 80 MB, the same every time (a fixed seed), so no
data file is committed.

    python3 obx/bench/json/gen_data.py OUT [MB]

One top-level array of records shaped like an API dump: ids, names and free text (with
escapes and non-ASCII text now and then), prices as decimals, floats, booleans, nulls,
nested address objects, tag arrays and small arrays of line items. Every number is one
that Python, jq and obx all write back as it was written, so the outputs can be compared.
"""
import json, random, sys

WORDS = '''alpha beta gamma delta order invoice customer account balance ledger entry total
amount payment refund shipment parcel warehouse region north south east west quarterly report
note comment status pending settled cancelled review approved draft final summary detail'''.split()
CITIES = ['London', 'Paris', 'Zürich', 'São Paulo', 'Tōkyō', 'New York', 'Kraków', 'Reykjavík']
ODD = ['line\nbreak', 'tab\there', 'quote "q"', 'back\\slash', 'emoji \U0001F600', 'nul \u0001 ctl',
       'snowman ☃', 'cjk 中文']


def text(r, n):
    s = ' '.join(r.choice(WORDS) for _ in range(n))
    if r.random() < 0.1:
        s += ' ' + r.choice(ODD)
    return s


def record(r, i):
    items = []
    for k in range(r.randint(0, 4)):
        items.append({'sku': 'SKU-%06d' % r.randint(0, 999999), 'qty': r.randint(1, 20),
                      'price': '%d.%02d' % (r.randint(0, 999), r.randint(0, 99))})
    return {
        'id': i,
        'uuid': '%08x-%04x-%04x' % (r.getrandbits(32), r.getrandbits(16), r.getrandbits(16)),
        'name': text(r, 2).title(),
        'active': r.random() < 0.7,
        'score': round(r.uniform(-1000, 1000), r.randint(0, 6)),
        'ratio': r.random(),
        'balance': None if r.random() < 0.1 else r.randint(-10**12, 10**12),
        'address': {'street': '%d %s Street' % (r.randint(1, 999), r.choice(WORDS).title()),
                    'city': r.choice(CITIES), 'zip': '%05d' % r.randint(0, 99999)},
        'tags': [r.choice(WORDS) for _ in range(r.randint(0, 5))],
        'items': items,
        'note': text(r, r.randint(3, 25)),
    }


def main():
    out = sys.argv[1]
    mb = float(sys.argv[2]) if len(sys.argv) > 2 else 80
    r = random.Random(20261105)
    limit = int(mb * 1024 * 1024)
    size = 0
    with open(out, 'w', encoding='utf-8') as f:
        f.write('[\n')
        i = 0
        while size < limit:
            line = json.dumps(record(r, i), ensure_ascii=False, separators=(',', ':'))
            line = fix_prices(line)
            f.write(('' if i == 0 else ',\n') + line)
            size += len(line) + 2
            i += 1
        f.write('\n]\n')


def fix_prices(line):
    # prices as JSON numbers with their trailing zeros, as a ledger writes them:
    # "price":"12.50" -> "price":12.50
    out = []
    j = 0
    while True:
        k = line.find('"price":"', j)
        if k < 0:
            out.append(line[j:])
            return ''.join(out)
        out.append(line[j:k + len('"price":')])
        e = line.index('"', k + len('"price":"'))
        out.append(line[k + len('"price":"'):e])
        j = e + 1


if __name__ == '__main__':
    main()
