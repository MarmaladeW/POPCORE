"""
report_names.py — 名称记忆 Name picks: which product a name written in a daily report means.

A manager answers a name once, in the paste review or on the Name picks page, and every later paste
remembers it, in every section (owner, 2026-11-03). A pick confirms a line by itself only in a
situation a manager confirmed it in: the same written name and note, the same store and section (and
display), with the product's identity unchanged. Anywhere else the paste asks again with the pick
already chosen. A different product replaces the old pick; one report picking two products for one
name keeps both as a dispute. Picks are report-only: they never become global product aliases.
"""
from matcher import load_report_picks, report_identity_signature

# How a report lists its names: what a next paste would still ask about first.
STATUSES = ('open', 'disputed', 'stale', 'confirm', 'remembered', 'ignored')
_PRODUCT_FIELDS = ('id', 'sku', 'jizhanming', 'name_cn_en', 'series_name', 'ip_series', 'design_name',
                   'stock_form', 'stock_unit')


def place_of(item) -> tuple[str, bool]:
    """The section and display a report line was read (or saved) in."""
    return item.get('section') or '', bool(item.get('to_display') or item.get('from_display'))


def status(pick, store_id, place, dismissed) -> str:
    if not pick:
        return 'ignored' if dismissed else 'open'
    if pick['state'] != 'ok':
        return pick['state']
    return 'remembered' if (store_id, *place) in pick['situations'] else 'confirm'


def save_pick(cur, key, raw_name, note, product_ids, actor_sub, store_id=None, places=()):
    """Remember what key means. A different product, or one whose identity changed since, replaces the
    old pick and forgets where it was confirmed; then each (section, display) in places is confirmed at
    store_id. Two product_ids keep the name disputed."""
    marks = ','.join('?' * len(product_ids))
    catalog = {row['id']: dict(row) for row in cur.execute(f'SELECT * FROM products WHERE id IN ({marks})', product_ids)}
    signatures = {pid: report_identity_signature(catalog[pid]) for pid in product_ids}
    current = {row['product_id']: row['identity_signature'] for row in cur.execute(
        'SELECT product_id, identity_signature FROM report_match_choices WHERE name_key=? AND note_key=?', key)}
    if current != signatures:
        cur.execute('DELETE FROM report_match_choices WHERE name_key=? AND note_key=?', key)
    for pid in product_ids:
        cur.execute("""INSERT INTO report_match_choices
            (name_key, note_key, product_id, identity_signature, raw_name, note, picked_by, picked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(name_key, note_key, product_id) DO UPDATE SET raw_name=excluded.raw_name,
                note=excluded.note, picked_by=excluded.picked_by, picked_at=excluded.picked_at""",
            (*key, pid, signatures[pid], raw_name, note, actor_sub))
    if len(product_ids) == 1 and store_id is not None:
        for section, display in places:
            cur.execute("""INSERT INTO report_match_situations
                (name_key, note_key, product_id, store_id, section, display, confirmed_by) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name_key, note_key, product_id, store_id, section, display)
                DO UPDATE SET confirmed_by=excluded.confirmed_by, confirmed_at=datetime('now')""",
                (*key, product_ids[0], store_id, section, int(display), actor_sub))
    cur.execute('DELETE FROM report_name_dismissals WHERE name_key=? AND note_key=?', key)


def log_names(con, store_id, day, items, actor_sub):
    """A store's latest paste of a day replaces that day's names (a re-paste can drop or fix lines)."""
    con.execute('DELETE FROM report_name_log WHERE store_id=? AND report_date=?', (store_id, day))
    for position, (key, item) in enumerate(items):
        section, display = place_of(item)
        con.execute("""INSERT OR IGNORE INTO report_name_log
            (store_id, report_date, name_key, note_key, section, display, position, raw_name, note, qty, box_size,
             loose_qty, guess_product_id, parsed_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (store_id, day, *key, section, int(display), position, item['raw_name'].strip(),
             (item.get('note') or '').strip(), item.get('qty') or 0, item.get('box_size'), item.get('loose_qty') or 0,
             (item.get('product') or {}).get('id'), actor_sub))


def _products(con, ids) -> dict:
    ids = sorted({pid for pid in ids if pid})
    if not ids:
        return {}
    rows = con.execute(f"""SELECT p.*, s.name AS series_name FROM products p
        LEFT JOIN product_series s ON s.id=p.series_id WHERE p.id IN ({','.join('?' * len(ids))})""", ids)
    return {row['id']: {field: row[field] for field in _PRODUCT_FIELDS} for row in rows}


def _pick_view(pick, products, codes) -> dict:
    return {'state': pick['state'], 'raw_name': pick['raw_name'], 'note': pick['note'],
            'products': [products[pid] for pid in pick['product_ids'] if pid in products],
            'places': sorted(({'store_code': codes[store_id], 'section': section, 'display': display}
                              for store_id, section, display in pick['situations'] if store_id in codes),
                             key=lambda place: (place['store_code'], place['section'], place['display'])),
            'picked_at': pick['picked_at']}


def _dismissed(con) -> set:
    return {(row[0], row[1]) for row in con.execute('SELECT name_key, note_key FROM report_name_dismissals')}


def report_days(con, stores) -> list:
    """Each store's pasted days, newest first, with how many names are in each status."""
    if not stores:
        return []
    picks, dismissed = load_report_picks(con), _dismissed(con)
    codes = {store['id']: store['code'] for store in stores}
    days = {}
    for row in con.execute(f"""SELECT * FROM report_name_log WHERE store_id IN ({','.join('?' * len(codes))})""",
                           list(codes)):
        key = (row['name_key'], row['note_key'])
        day = days.setdefault((row['store_id'], row['report_date']), {
            'store_code': codes[row['store_id']], 'date': row['report_date'], 'parsed_at': row['parsed_at'],
            'counts': dict.fromkeys(STATUSES, 0)})
        day['counts'][status(picks.get(key), row['store_id'], (row['section'], bool(row['display'])), key in dismissed)] += 1
        day['parsed_at'] = max(day['parsed_at'], row['parsed_at'])
    saved = {(row[0], row[1]) for row in con.execute('SELECT store, date FROM daily_report_metadata')}
    for day in days.values():
        day['saved'] = (day['store_code'], day['date']) in saved
    return sorted(days.values(), key=lambda day: (day['date'], day['parsed_at']), reverse=True)


def report_names(con, store, day) -> list:
    """One pasted day's names in paste order, each with its status, the paste's guess and any pick."""
    picks, dismissed = load_report_picks(con), _dismissed(con)
    rows = [dict(row) for row in con.execute(
        'SELECT * FROM report_name_log WHERE store_id=? AND report_date=? ORDER BY position', (store['id'], day))]
    codes = {row['id']: row['code'] for row in con.execute('SELECT id, code FROM stores')}
    products = _products(con, [row['guess_product_id'] for row in rows] + [
        pid for row in rows for pid in (picks.get((row['name_key'], row['note_key'])) or {}).get('product_ids', [])])
    names = []
    for row in rows:
        key = (row['name_key'], row['note_key'])
        pick = picks.get(key)
        names.append({**{field: row[field] for field in ('name_key', 'note_key', 'raw_name', 'note', 'section',
                                                          'qty', 'box_size', 'loose_qty')},
                      'display': bool(row['display']),
                      'status': status(pick, store['id'], (row['section'], bool(row['display'])), key in dismissed),
                      'guess': products.get(row['guess_product_id']),
                      'pick': _pick_view(pick, products, codes) if pick else None})
    return names


def all_picks(con) -> list:
    """Every remembered name, the most recently picked first."""
    picks = load_report_picks(con)
    codes = {row['id']: row['code'] for row in con.execute('SELECT id, code FROM stores')}
    products = _products(con, [pid for pick in picks.values() for pid in pick['product_ids']])
    rows = [{'name_key': key[0], 'note_key': key[1], **_pick_view(pick, products, codes)} for key, pick in picks.items()]
    rows.sort(key=lambda row: row['raw_name'].casefold())
    rows.sort(key=lambda row: row['picked_at'] or '', reverse=True)
    return rows
