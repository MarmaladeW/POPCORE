"""POP MART 价格 POP MART prices (owner, 2026-11-25, MarmaladeW/popcore-work#91): every Monday at 6:00 AM Toronto
time POPCORE reads POP MART Canada and POP MART US, every product's name, price and in stock or sold out, and the
Products → POP MART prices screen sets them beside POPCORE's prices: same, we are higher, we are lower, not found.
It changes no price and sends nothing to the website.

POP MART's search pages carry their data in the page (`__NEXT_DATA__` → `serverSearchRaw`), 100 products a page, so
a plain request reads them; no browser. Seven broad words reach about 1,030 CA and 1,310 US products in about 45 s
each. Stock is a flag (0, 1, 2), never a count. POP MART's product numbers differ per region, so products are joined by
name, never by number. See docs/popmart-prices.md."""
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

import db

TORONTO = ZoneInfo('America/Toronto')
REGIONS = {'ca': 'CAD', 'us': 'USD'}
TERMS = ('Series', 'Figure', 'Plush', 'Pendant', 'Box', 'MEGA', 'POP BEAN')
PAGE_SIZE = 100
MAX_PAGES = 30           # per word; "Series" needs 9 (Nov 2026)
PAUSE = 1.0              # seconds between pages: polite, and the whole read still fits in two minutes
WEEKDAY, HOUR = 0, 6     # Monday 6:00 AM, Toronto (owner, 2026-11-25)
RETRY_MINUTES = 60       # a failed weekly read tries again an hour later
STALE_MINUTES = 20       # a read still "running" after this was cut off (a restart)
KEEP_SHARE = 0.6         # a read with under 60% of the last good read's products is not trusted: the old list stays
REQUEST_KEY = 'popmart_read_requested'
USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/130.0 Safari/537.36')
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
FILLER = {'blind', 'box', 'the', 'pop', 'mart', 'x'}
KINDS = {'random_box': 'box', 'sealed_set': 'set', 'ordinary': 'item'}


class ReadError(Exception):
    pass


def _utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _sql(moment):
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _parse(text):
    return datetime.strptime(text, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc) if text else None


def norm(title):
    """A name as matching sees it: lower case, × read as x, & as and, no punctuation, no plural s, and without the words
    POP MART writes on some names and not others (Blind Box, The, POP MART)."""
    text = (title or '').lower().replace('’', "'").replace('&', ' and ').replace('×', ' x ')
    words = re.sub(r'[^a-z0-9]+', ' ', text).split()
    words = [w[:-1] if len(w) > 3 and w.endswith('s') and not w.endswith('ss') else w for w in words]
    return ' '.join(w for w in words if w not in FILLER)


def _option_kind(title):
    words = norm(title)
    if words in ('whole set', 'set'):
        return 'set'
    if words in ('single', 'single box', ''):
        return 'box'
    return 'other'  # QUANTITY, a phone model, a colour


# ── Reading POP MART ──────────────────────────────────────────────────────────

def fetch_page(region, term, page):
    url = f'https://www.popmart.com/{region}/search/{quote(term)}'
    response = requests.get(url, params={'page': page, 'pageSize': PAGE_SIZE}, timeout=30,
                            headers={'User-Agent': USER_AGENT, 'Accept-Language': 'en'})
    if response.status_code != 200:
        raise ReadError(f'POP MART answered {response.status_code} for {region.upper()} "{term}" page {page}')
    return response.text


def parse_page(html):
    """(total, products) of one search page."""
    found = NEXT_DATA.search(html or '')
    if not found:
        raise ReadError('A POP MART page had no product data: the site may have changed')
    try:
        data = json.loads(found.group(1))['props']['pageProps'].get('serverSearchRaw') or {}
        return int(data.get('total') or 0), list(data.get('list') or [])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ReadError('A POP MART page had product data POPCORE could not read: the site may have changed') from exc


def _cents(value):
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def read_region(region, fetch=fetch_page, sleep=time.sleep):
    """Every option POP MART lists in the region, by its option number."""
    options = {}
    for term in TERMS:
        for page in range(1, MAX_PAGES + 1):
            total, products = parse_page(fetch(region, term, page))
            for product in products:
                title = ' '.join(str(product.get('title') or '').split())
                if not title or product.get('id') is None:
                    continue
                for sku in product.get('skus') or []:
                    if sku.get('id') is None:
                        continue
                    price, sale = _cents(sku.get('price')), _cents(sku.get('discountPrice'))
                    stock = (sku.get('stock') or {}).get('onlineStock')
                    options[str(sku['id'])] = {
                        'popmart_id': str(product['id']), 'title': title, 'title_norm': norm(title),
                        'option_title': ' '.join(str(sku.get('title') or '').split()),
                        'price_cents': price, 'sale_cents': sale if sale and price and sale < price else None,
                        'in_stock': 1 if isinstance(stock, (int, float)) and stock > 0 else 0}
            if not products or page * PAGE_SIZE >= total:
                break
            sleep(PAUSE)
        sleep(PAUSE)
    return options


def _last_good(con, region):
    return con.execute("SELECT * FROM popmart_reads WHERE region=? AND status='ok' ORDER BY id DESC LIMIT 1",
                       (region,)).fetchone()


def run_read(con, trigger, regions=tuple(REGIONS), by=None, fetch=fetch_page, sleep=time.sleep, now=_utcnow):
    """Read each region and keep its list, unless the read failed or found far fewer products than the last good one:
    then the earlier list stays and the read says why. No write transaction is open while POP MART is asked."""
    for region in regions:
        cur = con.execute("INSERT INTO popmart_reads(region, trigger, started_by, started_at, status) VALUES (?, ?, ?, ?, 'running')",
                          (region, trigger, by, _sql(now())))
        read_id = cur.lastrowid
        con.commit()
        status, error, products, count = 'ok', None, 0, 0
        try:
            options = read_region(region, fetch=fetch, sleep=sleep)
            products, count = len({option['popmart_id'] for option in options.values()}), len(options)
            last = _last_good(con, region)
            floor = int((last['products'] or 0) * KEEP_SHARE) if last else 1
            if products < max(floor, 1):
                status = 'error'
                error = (f'POP MART gave only {products} products (the last good read had {last["products"]}); '
                         'the earlier list is kept.' if last else 'POP MART gave no products.')
        except (ReadError, requests.RequestException) as exc:
            status, error = 'error', str(exc)[:300] or type(exc).__name__
        if status == 'ok':
            con.execute('DELETE FROM popmart_items WHERE region=?', (region,))
            con.executemany(
                """INSERT INTO popmart_items(region, option_id, popmart_id, title, title_norm, option_title, price_cents,
                                             sale_cents, in_stock, read_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(region, option_id, o['popmart_id'], o['title'], o['title_norm'], o['option_title'], o['price_cents'],
                  o['sale_cents'], o['in_stock'], read_id) for option_id, o in options.items()])
        con.execute('UPDATE popmart_reads SET status=?, error=?, products=?, options=?, finished_at=? WHERE id=?',
                    (status, error, products, count, _sql(now()), read_id))
        con.commit()


# ── When to read ──────────────────────────────────────────────────────────────

def week_slot(now):
    """This week's Monday 6:00 AM Toronto time (the last one at or before now), in UTC."""
    local = now.astimezone(TORONTO)
    slot = (local - timedelta(days=(local.weekday() - WEEKDAY) % 7)).replace(hour=HOUR, minute=0, second=0, microsecond=0)
    if slot > local:
        slot -= timedelta(days=7)
    return slot.astimezone(timezone.utc)


def next_slot(now):
    return week_slot(now) + timedelta(days=7)


def _due_regions(con, now):
    slot, due = _sql(week_slot(now)), []
    for region in REGIONS:
        reads = con.execute('SELECT status, started_at FROM popmart_reads WHERE region=? AND started_at>=? ORDER BY id DESC',
                            (region, slot)).fetchall()
        if any(read['status'] == 'ok' for read in reads):
            continue
        if reads and _parse(reads[0]['started_at']) > now - timedelta(minutes=RETRY_MINUTES):
            continue
        due.append(region)
    return due


def _close_stale(con, now):
    con.execute("UPDATE popmart_reads SET status='error', error='The read stopped before it finished (a restart?).', finished_at=? "
                "WHERE status='running' AND started_at<?", (_sql(now), _sql(now - timedelta(minutes=STALE_MINUTES))))
    con.commit()


def connect():
    con = sqlite3.connect(db.DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    con.execute('PRAGMA busy_timeout = 10000')
    return con


def run_due(now=None, fetch=fetch_page, sleep=time.sleep):
    """Every minute from app.py, never two at once: a manager's Read now first, else the weekly read once Monday 6:00 AM
    has passed and this week's read has not worked yet (a failed one tries again an hour later)."""
    clock = (lambda: now) if now else _utcnow  # tests pass a moment; the server reads the clock
    now = clock()
    con = connect()
    try:
        _close_stale(con, now)
        if con.execute("SELECT 1 FROM popmart_reads WHERE status='running'").fetchone():
            return
        asked = con.execute('SELECT value FROM app_settings WHERE key=?', (REQUEST_KEY,)).fetchone()
        if asked:
            con.execute('DELETE FROM app_settings WHERE key=?', (REQUEST_KEY,))
            con.commit()
            run_read(con, 'manual', by=asked['value'] or None, fetch=fetch, sleep=sleep, now=clock)
            return
        regions = _due_regions(con, now)
        if regions:
            run_read(con, 'weekly', regions=regions, fetch=fetch, sleep=sleep, now=clock)
    finally:
        con.close()


def request_read(con, actor_sub):
    con.execute('INSERT INTO app_settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (REQUEST_KEY, actor_sub or ''))
    con.commit()


# ── The comparison ────────────────────────────────────────────────────────────

def _index(con):
    """region → name → (popmart_id, title, options). Two POP MART products with one name: the one with more options."""
    found = {region: {} for region in REGIONS}
    for row in con.execute('SELECT * FROM popmart_items ORDER BY region, popmart_id, option_id'):
        products = found[row['region']].setdefault(row['title_norm'], {})
        products.setdefault(row['popmart_id'], []).append(dict(row))
    return {region: {name: max(products.items(), key=lambda item: (len(item[1]), item[0]))
                     for name, products in names.items()} for region, names in found.items()}


def _pick(options, kind):
    """The POP MART option that is the same thing as ours: Whole Set for a set, Single box for a box, else the one
    option there is (or the cheapest)."""
    sets = [o for o in options if _option_kind(o['option_title']) == 'set']
    if kind == 'set':
        return sets[0] if sets else None
    singles = [o for o in options if _option_kind(o['option_title']) == 'box']
    if singles:
        return singles[0]
    rest = [o for o in options if o not in sets]
    priced = [o for o in rest if o['price_cents']]
    return min(priced, key=lambda o: o['price_cents']) if priced else (rest[0] if rest else None)


def _side(region, match, kind):
    if not match:
        return None
    popmart_id, options = match
    option = _pick(options, kind)
    base = {'popmart_id': popmart_id, 'title': options[0]['title'], 'currency': REGIONS[region],
            'url': f'https://www.popmart.com/{region}/products/{popmart_id}',
            'options': [o['option_title'] for o in options]}
    if not option:
        return {**base, 'option': None, 'price_cents': None, 'sale_cents': None, 'in_stock': None}
    return {**base, 'option': option['option_title'], 'price_cents': option['sale_cents'] or option['price_cents'],
            'regular_cents': option['price_cents'], 'sale_cents': option['sale_cents'], 'in_stock': bool(option['in_stock'])}


def _status(ours, ca):
    if not ca:
        return 'not_found'
    if ca['price_cents'] is None:
        return 'no_option'
    if ours is None:
        return 'no_price'
    return 'same' if ours == ca['price_cents'] else ('higher' if ours > ca['price_cents'] else 'lower')


def _suggest(name, names, limit=3):
    words = set(name.split())
    if not words:
        return []
    scored = []
    for other, title in names:
        theirs = set(other.split())
        share = len(words & theirs) / len(words | theirs)
        if share >= 0.5:
            scored.append((share, title))
    return [title for share, title in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]]


def listing(con, now=None):
    now = now or _utcnow()
    index = _index(con)
    links = {row['product_id']: row for row in con.execute('SELECT * FROM popmart_links')}
    products = [dict(row) for row in con.execute(
        """SELECT id, sku, name_cn_en, price, stock_form, boxes_per_dan FROM products
           WHERE sku LIKE 'PM%' AND stock_form IN ('random_box', 'ordinary', 'sealed_set') AND identity_status='verified'
           ORDER BY sku""")]
    by_sku = {product['sku']: product for product in products}
    ca_names = sorted({(name, match[1][0]['title']) for name, match in index['ca'].items()}, key=lambda item: item[1])
    rows = []
    for product in products:
        kind = KINDS[product['stock_form']]
        base = by_sku.get(product['sku'][:-4]) if kind == 'set' and product['sku'].endswith('-SET') else product
        base = base or product
        link = links.get(base['id'])
        name = link['title_norm'] if link else norm(re.sub(r'\s*·\s*Whole Set$', '', base['name_cn_en'] or ''))
        ca, us = (_side(region, index[region].get(name), kind) for region in ('ca', 'us'))
        ours = round(product['price'] * 100) if product['price'] else None
        status = _status(ours, ca)
        rows.append({
            'product_id': product['id'], 'sku': product['sku'], 'name': product['name_cn_en'], 'kind': kind,
            'base_id': base['id'], 'our_cents': ours, 'ca': ca, 'us': us, 'status': status,
            'diff_cents': ours - ca['price_cents'] if ours is not None and ca and ca['price_cents'] is not None else None,
            'linked': bool(link), 'linked_title': link['title'] if link else None,
            'suggestions': _suggest(name, ca_names) if status == 'not_found' and base is product and not link else []})
    counts = {key: sum(1 for row in rows if row['status'] == key)
              for key in ('same', 'higher', 'lower', 'not_found', 'no_option', 'no_price')}
    reads = {}
    for region in REGIONS:
        last = con.execute('SELECT * FROM popmart_reads WHERE region=? ORDER BY id DESC LIMIT 1', (region,)).fetchone()
        good = _last_good(con, region)
        reads[region] = {'last': dict(last) if last else None, 'good_at': good['finished_at'] if good else None,
                         'products': good['products'] if good else 0, 'options': good['options'] if good else 0}
    asked = con.execute('SELECT 1 FROM app_settings WHERE key=?', (REQUEST_KEY,)).fetchone()
    running = con.execute("SELECT 1 FROM popmart_reads WHERE status='running'").fetchone()
    return {'rows': rows, 'counts': counts, 'reads': reads, 'titles': [title for _, title in ca_names],
            'next_read': _sql(next_slot(now)), 'requested': bool(asked), 'running': bool(running)}


def link(con, product_id, title, actor_sub):
    """A manager's own match for a product whose name differs from POP MART's: the POP MART name it is. A whole set
    follows its box."""
    product = con.execute("SELECT id, sku, stock_form FROM products WHERE id=? AND sku LIKE 'PM%'", (product_id,)).fetchone()
    if not product or product['stock_form'] not in ('random_box', 'ordinary'):
        raise ValueError('Only a POP MART box or item can be linked; a whole set follows its box.')
    title = ' '.join(str(title or '').split())
    if not title or not con.execute('SELECT 1 FROM popmart_items WHERE title_norm=? LIMIT 1', (norm(title),)).fetchone():
        raise ValueError('That name is not on POP MART’s list.')
    con.execute("""INSERT INTO popmart_links(product_id, title, title_norm, linked_by, linked_at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(product_id) DO UPDATE SET title=excluded.title, title_norm=excluded.title_norm,
                   linked_by=excluded.linked_by, linked_at=excluded.linked_at""",
                (product_id, title, norm(title), actor_sub, _sql(_utcnow())))
    con.commit()


def unlink(con, product_id):
    con.execute('DELETE FROM popmart_links WHERE product_id=?', (product_id,))
    con.commit()
