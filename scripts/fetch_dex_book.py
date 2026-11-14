"""图鉴 Dex step 3: read the website's book (contract section 5) into a book-export.json, the file
scripts/load_dex_book.py reads, for the check before switch-over (docs/dex-book.md).

It only reads: it pages through GET {WEBSITE_URL}/wp-json/popcore/v1/book with the stock sync's key (WEBSITE_URL,
WEBSITE_KEY, WEBSITE_SECRET from the environment) and writes {"made_at": ..., "entries": [...]}. It never writes to
the website or to POPCORE's database. --out must not exist yet.

    WEBSITE_URL=https://popcore.ca WEBSITE_KEY=... WEBSITE_SECRET=... python scripts/fetch_dex_book.py --out book-export.json
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'popcore_app'))
os.environ.setdefault('AUTH0_DOMAIN', 'dex-fetch.invalid')
os.environ.setdefault('DISABLE_SCHEDULER', '1')

import dex_sync  # noqa: E402
from website_sync import WebsiteError  # noqa: E402

PER_PAGE = 500  # the most the route gives per page
MAX_PAGES = 1000


def _entries(answer):
    """A page is a list of entries, or {"entries": [...]}."""
    if isinstance(answer, list):
        return answer
    if isinstance(answer, dict) and isinstance(answer.get('entries'), list):
        return answer['entries']
    raise WebsiteError('The website’s book page could not be read.', 'website_answer')


def fetch(cfg):
    entries, page = [], 1
    while page <= MAX_PAGES:
        answer, headers = dex_sync.call(cfg, 'GET', '/book', params={'page': page, 'per_page': PER_PAGE})
        rows = _entries(answer)
        entries += [row for row in rows if isinstance(row, dict)]
        total = (headers or {}).get('X-WP-TotalPages')
        if (total is not None and str(total).isdigit() and page >= int(total)) or len(rows) < PER_PAGE:
            return entries
        page += 1
    raise WebsiteError(f'The website’s book has more than {MAX_PAGES} pages; stopped.', 'website_answer')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', required=True, type=Path, help='the book-export.json to write (must not exist)')
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f'{args.out} already exists')
    cfg, problem = dex_sync.settings()
    if cfg is None:
        parser.error(problem)
    try:
        entries = fetch(cfg)
    except WebsiteError as exc:
        print(f'Not written: {exc}', file=sys.stderr)
        return 1
    made_at = datetime.now(timezone.utc).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as handle:
        json.dump({'made_at': made_at, 'entries': entries}, handle, ensure_ascii=False, indent=1)
    kinds = {}
    for entry in entries:
        kinds[entry.get('kind')] = kinds.get(entry.get('kind'), 0) + 1
    print(json.dumps({'out': str(args.out), 'made_at': made_at, 'entries': len(entries), 'by_kind': kinds}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
