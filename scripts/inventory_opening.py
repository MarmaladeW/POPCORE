"""Export opening count sheets and review or apply completed opening counts.

Dry-run is the default: the database is opened read-only and a JSON report
compares every count with legacy stock. ``--apply`` additionally needs a
business date, an admin Auth0 subject with inventory access to every store,
and a new backup path; see docs/inventory-core.md (Opening counts).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import closing
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / 'popcore_app'
sys.path.insert(0, str(APP_DIR))
os.environ.setdefault('AUTH0_DOMAIN', 'opening-count.invalid')
os.environ.setdefault('DISABLE_SCHEDULER', '1')

from inventory_opening import (  # noqa: E402
    COUNT_SHEET_SCOPES, DEFAULT_OPENING_REASON, OpeningCountError,
    apply_opening_counts, connect, count_sheet_csv, count_sheet_rows,
    read_count_file, review_opening_counts,
)


def _existing_database(path: Path) -> Path:
    path = path.resolve()
    if not path.is_file():
        raise SystemExit(f'database does not exist: {path}')
    return path


def export_sheet(args):
    database = _existing_database(args.db)
    if args.output.exists():
        raise SystemExit(f'refusing existing output: {args.output}')
    with closing(connect(database, read_only=True)) as con:
        rows = count_sheet_rows(con, args.store, args.location, args.scope)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(count_sheet_csv(rows), encoding='utf-8')
    print(f'{len(rows)} rows written to {args.output}')
    return rows


def intake(args):
    database = _existing_database(args.db)
    rows = []
    for path in args.counts:
        rows.extend(read_count_file(path))
    if args.apply:
        missing = [flag for flag, value in (('--business-date', args.business_date),
                                            ('--actor-sub', args.actor_sub),
                                            ('--backup', args.backup)) if not value]
        if missing:
            raise SystemExit(f'--apply also needs {", ".join(missing)}')
        report = apply_opening_counts(
            database, rows, actor_sub=args.actor_sub,
            business_date=date.fromisoformat(args.business_date).isoformat(),
            backup_path=args.backup, empty_locations=args.empty_location,
            reason=args.reason,
        )
    else:
        business_date = date.fromisoformat(
            args.business_date or date.today().isoformat()).isoformat()
        with closing(connect(database, read_only=True)) as con:
            report = review_opening_counts(
                con, rows, empty_locations=args.empty_location,
                actor_sub=args.actor_sub, business_date=business_date,
                reason=args.reason,
            )
        report['applied'] = False
        report['dry_run'] = True
    report['database'] = str(database)
    report['count_files'] = [str(path) for path in args.counts]
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding='utf-8')
    print(text)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)

    sheet = commands.add_parser('count-sheet', help='write a blind count sheet CSV')
    sheet.add_argument('--db', type=Path, required=True)
    sheet.add_argument('--store', required=True, help='DT or MK')
    sheet.add_argument('--location', required=True,
                       help='floor, upstairs (DT) or warehouse (MK)')
    sheet.add_argument('--scope', choices=COUNT_SHEET_SCOPES, default='legacy',
                       help='legacy: products with legacy stock at the store; catalog: every product')
    sheet.add_argument('--output', type=Path, required=True)
    sheet.set_defaults(handler=export_sheet)

    review = commands.add_parser('intake', help='review (default) or apply completed counts')
    review.add_argument('--db', type=Path, required=True)
    review.add_argument('counts', type=Path, nargs='+', help='completed count CSV files')
    review.add_argument('--empty-location', action='append', default=[],
                        metavar='STORE-LOCATION',
                        help='declare a location that was counted and holds nothing, e.g. MK-warehouse')
    review.add_argument('--business-date', help='count date (YYYY-MM-DD)')
    review.add_argument('--actor-sub', help='Auth0 subject of the admin approving the opening')
    review.add_argument('--reason', default=DEFAULT_OPENING_REASON)
    review.add_argument('--report', type=Path, help='also write the JSON report here')
    review.add_argument('--apply', action='store_true',
                        help='post the opening and switch to authoritative mode')
    review.add_argument('--backup', type=Path, help='new SQLite backup path (required with --apply)')
    review.set_defaults(handler=intake)

    args = parser.parse_args(argv)
    try:
        report = args.handler(args)
    except OpeningCountError as exc:
        raise SystemExit(f'{exc.code}: {exc}') from exc
    if args.command == 'intake' and not (report.get('applied') or report.get('ready_to_apply')
                                         or report.get('already_applied')):
        raise SystemExit(1)
    return report


if __name__ == '__main__':
    main()
