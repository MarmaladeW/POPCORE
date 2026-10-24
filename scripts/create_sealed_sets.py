"""Create a sealed set (整端) product for every verified blind box, linked as 1 set = N boxes.

Dry run is the default: the database is opened read-only and the report lists each sealed set that
would be created, the blind boxes still waiting for identity review, and any blocking problem.
``--apply`` needs a new ``--backup`` path; see docs/inventory-core.md (Sealed sets).
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
os.environ.setdefault('AUTH0_DOMAIN', 'sealed-sets.invalid')
os.environ.setdefault('DISABLE_SCHEDULER', '1')

from sealed_sets import SealedSetError, apply_sealed_sets, plan_sealed_sets  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='create the sealed sets')
    parser.add_argument('--backup', type=Path, help='new SQLite backup path (required with --apply)')
    parser.add_argument('--report', type=Path, help='also write the JSON report here')
    args = parser.parse_args(argv)
    database = args.db.resolve()
    if not database.is_file():
        raise SystemExit(f'database does not exist: {database}')
    try:
        if args.apply:
            if args.backup is None or args.backup.exists():
                raise SystemExit('--apply needs a --backup path that does not exist yet')
            report = apply_sealed_sets(str(database), backup_path=str(args.backup))
        else:
            with closing(sqlite3.connect(f'file:{database}?mode=ro', uri=True)) as con:
                con.row_factory = sqlite3.Row
                report = {**plan_sealed_sets(con), 'dry_run': True}
    except SealedSetError as exc:
        raise SystemExit(f'{exc.code}: {exc}') from exc
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(text, encoding='utf-8')
    print(text)
    if report['errors']:
        raise SystemExit(1)
    return report


if __name__ == '__main__':
    main()
