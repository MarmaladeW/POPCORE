"""图鉴 Dex: apply the owner's review decisions to the book (docs/dex-book.md, "Applying review decisions").

Dry run by default: every operation runs inside one transaction that is rolled back, and the report says what each
would do, what is already done, and what is refused and why. With --apply it first takes an online backup to
--backup (required, and the file must not exist yet), then applies every operation in ONE transaction; if any
operation is refused, nothing is written. Re-running is safe: an operation whose result already holds changes nothing.

    POPCORE_DB_PATH=/path/popcore.db python scripts/apply_dex_decisions.py --decisions book-decisions.json \
        --out report-dir [--apply --backup /path/before-dex-decisions.db]
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'popcore_app'))
os.environ.setdefault('AUTH0_DOMAIN', 'dex-decisions.invalid')
os.environ.setdefault('DISABLE_SCHEDULER', '1')

import db  # noqa: E402
import dex_decisions  # noqa: E402
from dex_book import DexError  # noqa: E402

COLUMNS = (('n', 'No.'), ('op', 'Operation'), ('status', 'What happened'), ('entry', 'Entry'), ('popcore_id', 'POPCORE ID'),
           ('result', 'What it did or would do'), ('card', 'Card or decision'), ('reason', 'Why'))


def write_sheet(result, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    book = Workbook()
    summary = book.active
    summary.title = 'Summary'
    summary.append(['Operation', 'What happened', 'How many'])
    for op, statuses in sorted(result['counts'].items()):
        for status, count in sorted(statuses.items()):
            summary.append([op, status, count])
    for title, rows in (('Operations', result['report']), ('Refused', result['refused'])):
        sheet = book.create_sheet(title)
        sheet.append([header for _, header in COLUMNS])
        for row in rows:
            sheet.append([_safe(row.get(field)) for field, _ in COLUMNS])
    for sheet in book.worksheets:
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = 'A2'
        for column in sheet.columns:
            width = max(len(str(cell.value or '')) for cell in list(column)[:500])
            sheet.column_dimensions[column[0].column_letter].width = min(max(width + 2, 8), 80)
    book.save(path)


def _safe(value):
    """A spreadsheet must not read a name as a formula."""
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@'):
        return "'" + value
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--decisions', required=True, help='the decisions file (JSON list of operations)')
    parser.add_argument('--out', required=True, help='folder for the report (JSON and sheet)')
    parser.add_argument('--apply', action='store_true', help='write the operations (after the backup)')
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
    try:
        operations = dex_decisions.read_decisions(args.decisions)
    except (OSError, ValueError, DexError) as exc:
        parser.error(f'Cannot read {args.decisions}: {exc}')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(database), timeout=db.SQLITE_BUSY_TIMEOUT)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='dex_entries'").fetchone() is None:
            parser.error('This database has no dex_entries table yet; start the app once so its migrations run')
        if args.apply:
            backup = sqlite3.connect(args.backup)
            try:
                con.backup(backup)
            finally:
                backup.close()
        result = dex_decisions.run(con, operations, apply=args.apply)
    finally:
        con.close()

    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    report_json = out / f'dex-decisions-{stamp}.json'
    report_sheet = out / f'dex-decisions-{stamp}.xlsx'
    result.update(database=str(database), decisions=args.decisions, backup=args.backup if args.apply else None)
    report_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    write_sheet(result, report_sheet)
    statuses = Counter(line['status'] for line in result['report'])
    print(json.dumps({'database': str(database), 'dry_run': result['dry_run'], 'applied': result['applied'],
                      'operations': result['operations'], 'by_status': dict(statuses), 'counts': result['counts'],
                      'refused': [{key: line[key] for key in ('n', 'op', 'entry', 'result', 'card')} for line in result['refused']],
                      'backup': result['backup'], 'report': str(report_json), 'sheet': str(report_sheet)},
                     ensure_ascii=False, indent=1))
    return 1 if result['refused'] else 0


if __name__ == '__main__':
    sys.exit(main())
