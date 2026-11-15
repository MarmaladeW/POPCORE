"""Open the sealed sets (整端) sitting on a floor into boxes, so sales can take them.

A floor holds blind boxes as boxes only; 补货 used to bring a picked whole set down as it was. This lists every
sealed set with saleable stock on a live store's floor and the boxes it becomes (1 set = N boxes, by the set's
recorded size), and with --apply turns them into loose boxes on the same floor.

Dry run is the default: the database is opened read-only and nothing is written. --apply needs a new --backup
path (a checked SQLite backup is taken first, under the same write lock) and --actor-sub (the Auth0 subject of
the manager or admin the change is recorded under; they need inventory access for each store). Everything is
one transaction: a failed check rolls it all back. A sealed set whose size was never recorded is listed under
"left as it is" and not touched. A second run finds nothing left to open.

    python scripts/open_floor_sets.py --db /path/popcore.db                      # what it would do
    python scripts/open_floor_sets.py --db /path/popcore.db --store DT           # one store only
    python scripts/open_floor_sets.py --db /path/popcore.db --apply \\
        --backup /path/before-floor-sets.db --actor-sub 'auth0|...'

See docs/goods-handling.md (Whole sets on the floor).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'popcore_app'))
os.environ.setdefault('AUTH0_DOMAIN', 'floor-sets.invalid')
os.environ.setdefault('DISABLE_SCHEDULER', '1')

from floor_sets import FloorSetError, apply_floor_sets, plan_floor_sets  # noqa: E402


def print_report(report):
    applied = report.get('applied')
    print('APPLIED: the sealed sets below are now boxes.' if applied
          else 'DRY RUN: nothing was changed. This is what --apply would do.')
    if applied:
        print(f"Backup taken first: {report['backup']}")
        print(f"Business date {report['business_date']}; inventory documents: "
              + ', '.join(f'{store} #{document}' for store, document in report['documents'].items()))
    print()
    if report['items']:
        print(f"{'Store':<6}{'Sealed set':<16}{'Sets on floor':>14}{'x':>3}{'per set':>8}  "
              f"{'Box':<12}{'Floor boxes':>12}{'->':>4}{'after':>7}  Name")
        for item in report['items']:
            sets = f"{item['sets']} -> 0"
            print(f"{item['store']:<6}{item['set_sku'] or '-':<16}{sets:>14}{'x':>3}{item['boxes_per_set']:>8}  "
                  f"{item['box_sku'] or '-':<12}{item['floor_boxes_before']:>12}{'->':>4}{item['floor_boxes_after']:>7}  "
                  f"{item['name']}")
        totals = report['totals']
        print(f"\n{totals['set_products']} sealed set products, {totals['sets']} sets -> {totals['boxes']} boxes, "
              f"at {', '.join(totals['stores'])}.")
    else:
        print('No sealed set with saleable stock is on a floor.')
    if report['blocked']:
        print('\nLeft as it is (fix in 产品 Products, then run again):')
        for item in report['blocked']:
            print(f"  {item['store']} {item['set_sku'] or '-'} {item['name']}: {item['sets']} sets. {item['message']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--store', action='append', default=[], metavar='CODE',
                        help='only this store, e.g. DT (repeatable; default: every live store)')
    parser.add_argument('--apply', action='store_true', help='open the sealed sets into boxes')
    parser.add_argument('--backup', type=Path, help='new SQLite backup path (required with --apply)')
    parser.add_argument('--actor-sub', help='Auth0 subject of the manager or admin this is recorded under')
    parser.add_argument('--business-date', help='date on the inventory documents (default: today in Toronto)')
    parser.add_argument('--report', type=Path, help='also write the JSON report here')
    args = parser.parse_args(argv)
    database = args.db.resolve()
    if not database.is_file():
        raise SystemExit(f'database does not exist: {database}')
    stores = args.store or None
    try:
        if args.apply:
            if args.backup is None or args.backup.exists():
                raise SystemExit('--apply needs a --backup path that does not exist yet')
            if not args.actor_sub:
                raise SystemExit('--apply needs --actor-sub')
            report = apply_floor_sets(str(database), backup_path=str(args.backup), actor_sub=args.actor_sub,
                                      stores=stores, business_date=args.business_date)
        else:
            with closing(sqlite3.connect(f'file:{database}?mode=ro', uri=True)) as con:
                con.row_factory = sqlite3.Row
                report = {**plan_floor_sets(con, stores), 'dry_run': True}
    except FloorSetError as exc:
        raise SystemExit(f'{exc.code}: {exc}') from exc
    print_report(report)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    main()
