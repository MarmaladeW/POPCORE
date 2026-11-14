"""图鉴 Dex step 2: load the website's book export into POPCORE's book (docs/dex-book.md, "The load").

Dry run by default: reads the database, writes nothing to it, and writes the review sheet. With --apply it first
takes an online backup to --backup (required, and the file must not exist yet), then applies the plan in one
transaction, then prints the summary. Re-running is safe: rows a person changed are left alone and reported.

    POPCORE_DB_PATH=/path/popcore.db python scripts/load_dex_book.py --export book-export.json \
        --ip-list ip-list-checked.csv --out review-dir [--evidence pairing-evidence.csv] \
        [--apply --backup /path/before-dex-load.db]
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'popcore_app'))
os.environ.setdefault('AUTH0_DOMAIN', 'dex-load.invalid')
os.environ.setdefault('DISABLE_SCHEDULER', '1')

import db  # noqa: E402
import dex_load  # noqa: E402
from dex_book import DexError  # noqa: E402


def write_sheet(tabs, out_dir, stamp):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    book = Workbook()
    book.remove(book.active)
    for title, header, rows in tabs:
        sheet = book.create_sheet(title[:31])
        sheet.append(list(header))
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in rows:
            sheet.append([_safe(value) for value in row])
        sheet.freeze_panes = 'A2'
        for column, name in zip(sheet.columns, header):
            width = max([len(str(name))] + [len(str(cell.value or '')) for cell in list(column)[:500]])
            sheet.column_dimensions[column[0].column_letter].width = min(max(width + 2, 10), 70)
    path = Path(out_dir) / f'dex-load-review-{stamp}.xlsx'
    book.save(path)
    return path


def _safe(value):
    """A spreadsheet must not read a name as a formula."""
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@'):
        return "'" + value
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--export', required=True, help="the website's book-export.json")
    parser.add_argument('--ip-list', required=True, help="the owner's checked IP list (CSV)")
    parser.add_argument('--out', required=True, help='folder for the review sheet')
    parser.add_argument('--evidence', help='earlier linking work: claims that a POPCORE ID is a book entry (CSV)')
    parser.add_argument('--apply', action='store_true', help='write the plan (after the backup)')
    parser.add_argument('--backup', help='with --apply: where the online backup goes; must not exist yet')
    args = parser.parse_args(argv)

    if args.apply:
        if not args.backup:
            parser.error('--apply needs --backup PATH (a new file) for the backup taken first')
        if Path(args.backup).exists():
            parser.error(f'--backup {args.backup} already exists; give a new file')
    database = Path(db.DB_PATH)
    if not database.is_file():
        parser.error(f'No database at {database} (set POPCORE_DB_PATH)')
    Path(args.out).mkdir(parents=True, exist_ok=True)

    export = dex_load.read_export(args.export)
    ip_list = dex_load.read_ip_list(args.ip_list)
    evidence = dex_load.read_evidence(args.evidence) if args.evidence else None
    con = sqlite3.connect(str(database), timeout=db.SQLITE_BUSY_TIMEOUT)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='dex_entries'").fetchone() is None:
            parser.error('This database has no dex_entries table yet; start the app once so its migrations run')
        plan = dex_load.plan(con, export, ip_list, evidence)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        sheet = write_sheet(dex_load.sheet_tabs(con, plan, ip_list), args.out, stamp)
        result = {'database': str(database), 'export_made_at': export.get('made_at'), 'dry_run': not args.apply,
                  'evidence': args.evidence, 'evidence_claims': len(evidence or []),
                  'plan': dex_load.summary(plan), 'review_sheet': str(sheet)}
        if args.apply:
            backup = sqlite3.connect(args.backup)
            try:
                con.backup(backup)
            finally:
                backup.close()
            result['backup'] = args.backup
            try:
                result['written'] = dex_load.apply(con, plan)
            except DexError as exc:
                result['refused'] = {'code': exc.code, 'message': str(exc)}
                print(json.dumps(result, ensure_ascii=False, indent=1))
                return 1
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    finally:
        con.close()


if __name__ == '__main__':
    sys.exit(main())
