"""图鉴 Dex: give every book product its format, once, by the contract's rule (contract 1.9 section 4).

Every product goes to popcore.ca with a format from the website's list (dex_book.FORMATS); the sync holds a product with
none. The format of a product is decided in this order:
  1. The owner's pick, from --picks (a JSON object of entry id to format), when given.
  2. A format a person set on the entry in POPCORE: the entry was last changed by someone, not by the load, the owner's
     review scripts or this script. It stays.
  3. The type word of the POPCORE item linked to the product (TYPE_WORDS). 盲盒 says nothing; 盲盒（杂项） (blind boxes
     that are not figures) takes the name's answer, Merchandise when the name gives none, and goes to the owner when
     the name says Figures.
  4. The product's own name (name_format). Its answer stands only when the linked item's name gives no other clear one.
  5. Anything else goes to the owner, and the product is left with no format, so nothing is sent with a guess. The
     website's own values are not trusted as they stand (13 lines named plush pendant or earphone case hold its silent
     default, Figures); a product the rule settles gets the rule's value instead.

Dry run by default: one transaction, rolled back. With --apply it first takes an online backup to --backup (a new
file), then writes every change in ONE transaction through dex_book.update, as the actor format-rule. Re-running
changes nothing. The report (--out) is a JSON file with every product and a Markdown list of the owner's picks.

    POPCORE_DB_PATH=/path/popcore.db python scripts/fill_dex_formats.py --out report-dir [--picks picks.json] \\
        [--apply --backup /path/before-dex-formats.db]
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'popcore_app'))
os.environ.setdefault('AUTH0_DOMAIN', 'dex-formats.invalid')
os.environ.setdefault('DISABLE_SCHEDULER', '1')

import db  # noqa: E402
import dex_book  # noqa: E402

ACTOR = {'sub': 'format-rule'}
NOT_A_PERSON = ('load', 'owner-review', 'format-rule')  # the load, the owner's review scripts and this one
TYPE_WORDS = {'吊卡': 'Blister Card', '大娃': 'MEGA', '大娃 400%': 'MEGA', '大娃 1000%': 'MEGA',
              '周边': 'Merchandise', '配件': 'Merchandise', '积木': 'Merchandise'}
MIXED_BLIND_BOX = '盲盒（杂项）'
GOODS = (r'magnets?|key ?chains?|key ?rings?|key covers?|earphones?|airpods|phones?|cups?|mugs?|tumblers?|glass(es)?|'
         r'bottles?|bags?|pouch(es)?|purses?|cases?|stickers?|cards?|badges?|pins?|charms?|plates?|blankets?|pillows?|'
         r'cushions?|lamps?|night ?lights?|clocks?|jewel(le)?ry|stands?|lanyards?|straps?|hats?|caps?|socks?|'
         r't-?shirts?|umbrellas?|notebooks?|pens?|stationery|towels?|mirrors?|combs?|wallets?|coasters?|blocks?|'
         r'bricks?|candles?|cables?|clips?|holders?|grips?|rings?|storage|openers?')
FIGURES = r'figur(e|es|ine|ines)|mini ?figures?|hippers?|artist collection|scene ?sets?|dolls?|vinyl face'
# The product's own name, first answer wins (contract section 4, step 3).
BY_NAME = (('POP BEAN', r'pop ?bean'), ('MEGA', r'\bmega\b|\b(400|1000)%'), ('Blister Card', r'blister|hanging card'),
           ('Fragrance Blind Box', r'fragrance|scented|perfume|aroma'), ('Plush Pendants', r'pendant|pendent'))


def name_format(name):
    """(format, why) from a name. A name with both a goods word and a figure word, and none of the stronger words
    before them, settles nothing: (None, 'goods and figure words')."""
    text = ' '.join((name or '').casefold().split())
    for value, pattern in BY_NAME:
        if re.search(pattern, text):
            return value, f'name: {value}'
    goods = re.search(rf'\b({GOODS})\b', text)
    figure = re.search(rf'\b({FIGURES})\b', text)
    if goods and figure:
        return None, f'name has goods ({goods.group(0)}) and figure ({figure.group(0)}) words'
    if goods:
        return 'Merchandise', f'name: goods ({goods.group(0)})'
    if figure:
        return 'Figures', f'name: figure ({figure.group(0)})'
    return None, 'name says nothing'


def decide(row, picks):
    """(format or None, why, for_owner) for one product row."""
    if row['id'] in picks:
        return picks[row['id']], 'owner’s pick', False
    if row['format'] in dex_book.FORMATS and row['updated_by'] not in NOT_A_PERSON:
        return row['format'], 'set by a person in POPCORE', False
    type_word = (row['type_word'] or '').strip()
    if type_word in TYPE_WORDS:
        return TYPE_WORDS[type_word], f'type word {type_word}', False
    value, why = name_format(row['name'])
    if type_word == MIXED_BLIND_BOX:
        if value == 'Figures':
            return None, f'{MIXED_BLIND_BOX} (not figures), but the {why}', True
        return (value, why, False) if value else ('Merchandise', f'{MIXED_BLIND_BOX}, name says nothing', False)
    if value is None:
        return None, why, True
    item_value, item_why = name_format(row['item_name']) if row['item_name'] else (None, '')
    if item_value and item_value != value:
        return None, f'book {why}; the linked item’s {item_why}', True
    return value, why, False


def suggestion(row):
    """What the owner is shown beside a product the rule leaves open: the item's answer, else the website's value."""
    item_value, _ = name_format(row['item_name']) if row['item_name'] else (None, '')
    return item_value or (row['format'] if row['format'] in dex_book.FORMATS else None)


ROWS = """SELECT e.id, e.name, e.updated_by, e.website_id,
                 CASE WHEN json_valid(e.extras) THEN json_extract(e.extras, '$.format') END AS format,
                 p.sku, p.product_type AS type_word, COALESCE(NULLIF(p.name_cn_en, ''), p.jizhanming) AS item_name
          FROM dex_entries e LEFT JOIN products p ON p.id=e.product_id
          WHERE e.kind='product' AND e.deleted_at IS NULL ORDER BY e.id"""


def run(con, picks=None, apply=False):
    """Decide every live product's format and write the changes in one transaction (rolled back unless apply)."""
    picks = picks or {}
    con.execute('BEGIN IMMEDIATE')
    try:
        rows = [dict(row) for row in con.execute(ROWS)]
        unknown = sorted(set(picks) - {row['id'] for row in rows})
        if unknown:
            raise dex_book.DexError(f'Picks name entries that are not live products: {unknown}', 'invalid_input')
        report = []
        with dex_book.batch(con):
            for row in rows:
                value, why, for_owner = decide(row, picks)
                stored = row['format'] if isinstance(row['format'], str) else None
                if value != stored:
                    dex_book.update(con, row['id'], {'extras': {'format': value}}, ACTOR)
                place = [step['name'] for step in dex_book._path(con, {'id': row['id'], 'parent_id': _parent(con, row['id'])})]
                report.append({'id': row['id'], 'place': place, 'name': row['name'], 'popcore_id': row['sku'],
                               'item_name': row['item_name'], 'type_word': row['type_word'], 'website_id': row['website_id'],
                               'before': stored, 'format': value, 'why': why, 'for_owner': for_owner,
                               'suggestion': suggestion(row) if for_owner else None, 'changed': value != stored})
        if con.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('foreign key check failed')
    except Exception:
        con.rollback()
        raise
    if apply:
        con.commit()
    else:
        con.rollback()
    return report


def _parent(con, entry_id):
    return con.execute('SELECT parent_id FROM dex_entries WHERE id=?', (entry_id,)).fetchone()[0]


def read_picks(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('picks are a JSON object of entry id to format')
    picks = {}
    for key, value in data.items():
        if not str(key).isdigit() or value not in dex_book.FORMATS:
            raise ValueError(f'pick {key!r}: {value!r} is not one of {", ".join(dex_book.FORMATS)}')
        picks[int(key)] = value
    return picks


def owner_list(report):
    """The products no rule settles, as a Markdown table for the owner."""
    lines = ['| Ref | Where | Product | POPCORE item | Type word | Website now | Suggested | Why open |',
             '|---|---|---|---|---|---|---|---|']
    cell = lambda value: str(value or '—').replace('|', '/')  # noqa: E731
    for row in (row for row in report if row['for_owner']):
        item = f"{row['popcore_id']} {row['item_name']}" if row['popcore_id'] else None
        lines.append('| ' + ' | '.join(cell(value) for value in (
            row['id'], ' › '.join(row['place']), row['name'], item, row['type_word'], row['before'], row['suggestion'],
            row['why'])) + ' |')
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', required=True, help='folder for the report')
    parser.add_argument('--picks', help='the owner’s picks: a JSON object of entry id to format')
    parser.add_argument('--apply', action='store_true', help='write the changes (after the backup)')
    parser.add_argument('--backup', help='with --apply: where the online backup goes; must not exist yet')
    args = parser.parse_args(argv)
    if args.apply and (not args.backup or Path(args.backup).exists()):
        parser.error('--apply needs --backup PATH, a file that does not exist yet')
    database = Path(db.DB_PATH)
    if not database.is_file():
        parser.error(f'No database at {database} (set POPCORE_DB_PATH)')
    try:
        picks = read_picks(args.picks) if args.picks else {}
    except (OSError, ValueError) as exc:
        parser.error(f'Cannot read {args.picks}: {exc}')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(database), timeout=db.SQLITE_BUSY_TIMEOUT)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    try:
        if args.apply:
            backup = sqlite3.connect(args.backup)
            try:
                con.backup(backup)
            finally:
                backup.close()
        report = run(con, picks, apply=args.apply)
    finally:
        con.close()

    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    report_json = out / f'dex-formats-{stamp}.json'
    report_md = out / f'dex-formats-owner-{stamp}.md'
    report_json.write_text(json.dumps({'database': str(database), 'applied': args.apply,
                                       'backup': args.backup if args.apply else None, 'products': report},
                                      ensure_ascii=False, indent=1), encoding='utf-8')
    report_md.write_text(owner_list(report), encoding='utf-8')
    print(json.dumps({'database': str(database), 'applied': args.apply, 'backup': args.backup if args.apply else None,
                      'products': len(report), 'changed': sum(row['changed'] for row in report),
                      'for_owner': sum(row['for_owner'] for row in report),
                      'by_format': dict(Counter(row['format'] or 'none (owner)' for row in report)),
                      'report': str(report_json), 'owner_list': str(report_md)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
