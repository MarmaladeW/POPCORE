"""图鉴 Dex sync, step 3: POPCORE sends the book to popcore.ca (owner, 2026-11-14: "yes build the POPCORE -> popcore.ca
sync"), behind its own switch, off until an admin turns it on. The contract is docs/dex-book-contract.md (1.9.6).
Step 4, at the end of this file: listings for stocked items with no listing, from a preview list a manager approves
(contract sections 7 and 10).

- What needs sending: an entry whose version is above its sent_version. Every entry starts at 0, so the first run
  sends the whole book. A new price recorded for a linked item (design_prices for a confirmed design,
  product_price_history for any other item) or a new POPCORE ID on a linked item is noticed here and raises the
  entry's version, since neither raises it by itself (owner, 2026-11-14: "the price
  history chart on popcore.ca should show the price change recorded in POPCORE").
- Order: parents before children (company, partner, ip, character, series, product, design), and an entry goes only
  once everything it points at (parent, partner, character, named IP) was accepted, or goes earlier in the same
  batch. Deletes go last, and only once every live entry was accepted (a refused entry holds them: what the book
  moved out from under a deleted row must be on the website first). They go lowest level first, a parent only after
  the deletes under it were confirmed. A deleted row the website has is sent as deleted and removed once the website
  confirms; one the website never had is removed without sending.
- Batches of up to 50, one call at a time, as many as fit in about 20 seconds per pass. quiet until the first full
  send has finished.
- Answers: ok stores the website_id and the version sent and clears the error. refused or error stores the website's
  code and message with the version it was for; that entry is not sent again until it changes or a manager retries.
  A call that fails (unreachable, timeout, not 200, key refused) marks nothing; the reason is kept as the last error.
  While the website's own switch is off its book route answers 503 book_switch_off: a failed call like any other.
- Held (contract 1.9 section 4, "nothing is sent with a guess"): a live product with no format from the website's list
  is not sent. It waits as held, with what sits under it behind it, until a manager picks its format in the editor.
- It reads website_sync.config() for the website's address and key and makes its own calls. It never touches stock,
  prices, orders or the stock sync's state.
"""
import json
import logging
import time
from datetime import datetime, timezone

import requests

import design_prices
import dex_book
import website_sync
from website_sync import WebsiteError

log = logging.getLogger(__name__)

ROUTE = '/wp-json/popcore/v1'
BATCH = 50
PASS_SECONDS = 20
TIMEOUT = (5, 60)  # the website answers within 30 seconds; POPCORE waits 60 (contract section 3)
ORDER = {kind: index for index, kind in enumerate(dex_book.KINDS)}
REFS = ('parent_id', 'partner_id', 'character_id', 'ip_id')
REFUSED_LIST = 200
HELD = ('format_missing', 'No format yet (形式): pick one in the editor, then it is sent.')
HALF_MISSING = ('The website’s half of the book sync is not there yet: popcore.ca does not answer its book route. '
                'Nothing was sent.')
LISTINGS_MISSING = ('popcore.ca does not answer its listings route (catalog 1.3.2 or later makes it). Nothing was made.')
# The whole listings call turned down (contract section 7): a failed call, like any other.
LISTINGS_CALL_CODES = ('listings_malformed', 'dex_missing')


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%S')


def _begin(con):
    if con.in_transaction:
        con.commit()
    con.execute('BEGIN IMMEDIATE')


# ── State ────────────────────────────────────────────────────────────────────

def state(con, key, default=None):
    row = con.execute('SELECT value FROM dex_sync_state WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def set_state(con, key, value):
    if value is None:
        con.execute('DELETE FROM dex_sync_state WHERE key=?', (key,))
    else:
        con.execute('INSERT INTO dex_sync_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (key, str(value)))


def enabled(con):
    return state(con, 'enabled') == '1'


def settings():
    """(cfg, problem): the website's address and key from the stock sync's settings, or None and why."""
    try:
        cfg = website_sync.config()
    except ValueError as exc:
        return None, str(exc)
    return cfg, None if cfg else 'No website key is set on the server (WEBSITE_URL, WEBSITE_KEY, WEBSITE_SECRET).'


# ── Calls to the website's book routes ───────────────────────────────────────

def call(cfg, method, path, *, params=None, body=None, files=None, data=None):
    """One call to popcore.ca's book routes with the stock sync's key; returns (data, headers).
    POST /book and POST /listings count only HTTP 200 (contract sections 3 and 7); the others any 2xx. A listings call
    the website turns down whole keeps the website's code (400 listings_malformed, 503 dex_missing)."""
    try:
        response = requests.request(method, f"{cfg['url']}{ROUTE}{path}", params=params, json=body, files=files,
                                    data=data, auth=(cfg['key'], cfg['secret']), timeout=TIMEOUT,
                                    allow_redirects=False, headers={'User-Agent': 'POPCORE Dex sync'})
    except requests.RequestException as exc:
        raise WebsiteError('The website cannot be reached.', 'website_unavailable') from exc
    try:
        answer = response.json()
    except ValueError:
        answer = None
    status = response.status_code
    if status == 404:
        raise WebsiteError(LISTINGS_MISSING if path == '/listings' else HALF_MISSING, 'website_half_missing', 404)
    if status in (401, 403):
        raise WebsiteError('The website refused POPCORE’s key on its book route. Its half of the book sync may not be '
                           'there yet, or the key is not allowed on it.', 'website_auth', status)
    if not (status == 200 if (method, path) in (('POST', '/book'), ('POST', '/listings')) else 200 <= status < 300):
        message = answer.get('message') if isinstance(answer, dict) else None
        code = answer.get('code') if isinstance(answer, dict) else None
        code = code if path == '/listings' and code in LISTINGS_CALL_CODES else 'website_error'
        raise WebsiteError(str(message or f'The website answered HTTP {status}.')[:300], code, status)
    return answer, response.headers


# ── Reading the book's sync facts ────────────────────────────────────────────

_ROWS = """SELECT e.id, e.kind, e.name, e.parent_id, e.partner_id, e.character_id, e.ip_id, e.version, e.sent_version,
                  e.send_error, e.deleted_at, e.website_id, e.website_item, e.sync_price_id, e.sync_popcore_id,
                  p.sku AS popcore_id, CASE WHEN json_valid(e.extras) THEN json_extract(e.extras, '$.format') END AS format,
                  CASE WHEN e.product_id IS NULL OR e.kind NOT IN ('product', 'design') THEN NULL
                       WHEN p.stock_form='confirmed_design' THEN
                           (SELECT MAX(d.id) FROM design_prices d WHERE d.product_id=e.product_id)
                       ELSE (SELECT MAX(h.id) FROM product_price_history h
                             WHERE h.product_id=e.product_id AND h.new_cents > 0) END AS price_id
           FROM dex_entries e LEFT JOIN products p ON p.id=e.product_id"""


def _error(row):
    try:
        found = json.loads(row['send_error']) if row['send_error'] else None
    except ValueError:
        found = None
    return found if isinstance(found, dict) else None


def classify(con):
    """{id: facts} for every row, each with `state`:
    sent (the website has this version), waiting, refused (at this version), held (a product with no format: POPCORE
    does not send it), behind (waits behind a refused or held entry), delete (a deleted row to send), drop (a deleted
    row the website never had: removed without sending)."""
    rows = {row['id']: dict(row) for row in con.execute(_ROWS)}
    for row in rows.values():
        row['drift'] = row['deleted_at'] is None and (
            row['price_id'] != row['sync_price_id'] or row['popcore_id'] != row['sync_popcore_id'])
        row['error'] = _error(row)
        due = row['version'] > row['sent_version'] or row['drift']
        refused = (row['error'] is not None and not row['drift']
                   and row['error'].get('version') == row['version'])
        if row['deleted_at'] is not None:
            had = row['website_id'] is not None or row['sent_version'] > 0
            row['state'] = 'refused' if had and refused else 'delete' if had else 'drop'
        else:
            held = due and row['kind'] == 'product' and row['format'] not in dex_book.FORMATS
            row['state'] = 'refused' if refused else 'held' if held else 'waiting' if due else 'sent'

    def blocked(row, seen):
        """True when something this entry points at was refused or held, or waits behind one."""
        for field in REFS:
            dep = rows.get(row[field])
            if dep is None or dep['id'] in seen:
                continue
            if dep['sent_version'] > 0 and dep['deleted_at'] is None:
                continue  # the website knows it
            if dep['state'] in ('refused', 'held') or blocked(dep, seen | {dep['id']}):
                return True
        return False
    for row in rows.values():
        if row['state'] == 'waiting' and blocked(row, {row['id']}):
            row['state'] = 'behind'
    return rows


def _counts(rows):
    counts = {'waiting': 0, 'sent': 0, 'refused': 0, 'held': 0, 'behind': 0, 'deletes': 0}
    for row in rows.values():
        key = {'delete': 'deletes', 'drop': None}.get(row['state'], row['state'])
        if key:
            counts[key] += 1
    counts['waiting'] += counts['deletes']
    return counts


def _place(rows, row):
    """Where an entry sits in the book (deleted rows included), top first, as names."""
    names, seen, parent = [], {row['id']}, row['parent_id']
    while parent is not None and parent not in seen and parent in rows:
        seen.add(parent)
        names.insert(0, rows[parent]['name'])
        parent = rows[parent]['parent_id']
    return names


def _refused_list(con, rows):
    found = []
    for row in sorted((row for row in rows.values() if row['state'] == 'refused'), key=lambda row: (ORDER[row['kind']], row['id'])):
        found.append({'id': row['id'], 'kind': row['kind'], 'name': row['name'], 'deleted': row['deleted_at'] is not None,
                      'place': _place(rows, row), 'code': row['error'].get('code'), 'message': row['error'].get('message'),
                      'at': row['error'].get('at')})
    return found[:REFUSED_LIST]


def _held_list(rows):
    """Products POPCORE holds for want of a format, in book order, with where they sit."""
    return [{'id': row['id'], 'kind': row['kind'], 'name': row['name'], 'place': _place(rows, row)}
            for row in sorted((row for row in rows.values() if row['state'] == 'held'), key=lambda row: row['id'])
            ][:REFUSED_LIST]


def status(con):
    cfg, problem = settings()
    rows = classify(con)
    error = state(con, 'last_error')
    return {
        'configured': cfg is not None, 'settings_problem': problem, 'url': cfg['url'] if cfg else None,
        'enabled': enabled(con), 'switched_at': state(con, 'switched_at'), 'switched_by': state(con, 'switched_by'),
        'last_run': state(con, 'last_run'), 'last_sent_at': state(con, 'last_sent_at'),
        'last_sent_count': int(state(con, 'last_sent_count', 0) or 0),
        'first_send_done': state(con, 'first_send_done') is not None,
        'last_error': json.loads(error) if error else None,
        'website_check': json.loads(state(con, 'website_check')) if state(con, 'website_check') else None,
        'counts': _counts(rows), 'refused': _refused_list(con, rows), 'held': _held_list(rows),
        # Deletes that wait because a live entry was refused or held (not merely because the rest has not gone yet).
        'deletes_held': (any(row['state'] == 'delete' for row in rows.values()) and any(
            row['deleted_at'] is None and row['state'] in ('refused', 'held', 'behind') for row in rows.values())),
    }


def preview(con):
    """What the next full send does: entries the website will create, update and delete, and the deletes by name with
    their place in the book. held: how many of those wait for a format (they go once they have one)."""
    rows = classify(con)
    live = [row for row in rows.values()
            if row['state'] in ('waiting', 'behind', 'refused', 'held') and row['deleted_at'] is None]
    create = sum(1 for row in live if row['website_id'] is None and row['sent_version'] == 0)
    deletes = sorted((row for row in rows.values() if row['state'] in ('delete', 'refused') and row['deleted_at']),
                     key=lambda row: (-ORDER[row['kind']], row['id']))
    return {'create': create, 'update': len(live) - create, 'delete': len(deletes),
            'held': sum(1 for row in live if row['state'] == 'held'),
            'removed_quietly': sum(1 for row in rows.values() if row['state'] == 'drop'),
            'deletes': [{'id': row['id'], 'kind': row['kind'], 'name': row['name'], 'website_id': row['website_id'],
                         'place': _place(rows, row)} for row in deletes]}


def entry_state(con, entry_id):
    rows = classify(con)
    row = rows.get(entry_id)
    if row is None or row['deleted_at'] is not None:
        raise dex_book.DexError('That Dex entry does not exist.', 'dex_not_found', 404)
    code, message = (row['error'].get('code'), row['error'].get('message')) if row['state'] == 'refused' else \
        HELD if row['state'] == 'held' else (None, None)
    return {'enabled': enabled(con), 'state': row['state'], 'version': row['version'], 'sent_version': row['sent_version'],
            'website_id': row['website_id'], 'code': code, 'message': message}


# ── What is sent ─────────────────────────────────────────────────────────────

def _price_history(con, product_id):
    """Every price recorded for the item, oldest first (contract section 4). Read only. A confirmed design's are the
    prices managers set (design_prices); any other item's are its price changes the database recorded
    (product_price_history; owner, 2026-11-14: "Yes, every item"), the same choice dex_book.price_history makes."""
    form = con.execute('SELECT stock_form FROM products WHERE id=?', (product_id,)).fetchone()
    if form is not None and form['stock_form'] == 'confirmed_design':
        query = 'SELECT cents, set_at AS at FROM design_prices WHERE product_id=? ORDER BY id'
    else:  # a change to no price, or to a price of zero, is not a point
        query = """SELECT new_cents AS cents, changed_at AS at FROM product_price_history
                   WHERE product_id=? AND new_cents > 0 ORDER BY id"""
    return [{'price': row['cents'] / 100, 'recorded_at': row['at'].replace(' ', 'T') + ('' if row['at'].endswith('Z') else 'Z')}
            for row in con.execute(query, (product_id,))]


def entry(con, entry_id, memo=None):
    """One entry as contract section 2 sends it."""
    memo = {} if memo is None else memo
    row = con.execute("""SELECT e.*, p.sku AS popcore_id FROM dex_entries e LEFT JOIN products p ON p.id=e.product_id
                         WHERE e.id=?""", (entry_id,)).fetchone()
    kind = row['kind']
    try:
        stored = json.loads(row['extras'] or '{}')
    except ValueError:
        stored = {}
    # 1.5: price_history replaces market_prices; POPCORE keeps market_prices for its own screen and never sends it.
    extras = {key: stored[key] for key in dex_book.EXTRAS[kind] if key in stored and key != 'market_prices'}
    for key in ('aliases', 'tags'):  # always a list (contract section 4); the load kept the website's text on a few
        if isinstance(extras.get(key), str):
            extras[key] = [part.strip() for part in extras[key].split(',') if part.strip()] or None
    if kind == 'design':  # always whole; empty when the shop has never priced it, so the chart shows only POPCORE's
        extras['price_history'] = _price_history(con, row['product_id']) if row['product_id'] is not None else []
    elif kind == 'product' and row['product_id'] is not None:  # a product the shop stocks (contract 1.6)
        extras['price_history'] = _price_history(con, row['product_id'])
    if row['website_id'] is not None:
        item = bool(row['website_item'])
    else:  # new to the website: a product with no series is an item figure there (contract section 2)
        parent = con.execute('SELECT kind FROM dex_entries WHERE id=?', (row['parent_id'],)).fetchone()
        item = kind == 'product' and (parent is None or parent['kind'] != 'series')
    shape = {
        'ref': row['id'], 'website_id': row['website_id'], 'kind': kind, 'parent_ref': row['parent_id'],
        'partner_ref': row['partner_id'], 'character_ref': row['character_id'],
        'ip_ref': row['ip_id'] if kind in dex_book.IP_KINDS else None,
        'name': row['name'], 'sort_order': row['sort_order'], 'picture_id': row['picture_id'],
        'blind_box': None if kind != 'product' or row['blind_box'] is None else bool(row['blind_box']),
        'item': item, 'rarity': row['rarity'] if kind == 'design' else None,
        'popcore_id': row['popcore_id'] if kind in ('product', 'design') else None,
        'extras': extras, 'version': row['version'], 'deleted': row['deleted_at'] is not None,
    }
    if kind in ('product', 'design'):
        ancestors = [] if row['deleted_at'] else dex_book._ancestors(con, row['parent_id'], memo)
        shape['full_name'] = dex_book.full_name(kind, row['name'], ancestors)
    return shape


# ── A pass ───────────────────────────────────────────────────────────────────

def notice(con):
    """A new recorded price for a linked item, or a new POPCORE ID on it: raise the version, so the entry is sent
    again (neither raises it by itself)."""
    found = [row for row in con.execute(_ROWS + ' WHERE e.deleted_at IS NULL').fetchall()
             if row['price_id'] != row['sync_price_id'] or row['popcore_id'] != row['sync_popcore_id']]
    if not found:
        return 0  # nothing new: only a read, so a pass never holds the till's database for writing without need
    _begin(con)
    try:
        for row in found:
            con.execute('UPDATE dex_entries SET version=version+1, sync_price_id=?, sync_popcore_id=? WHERE id=?',
                        (row['price_id'], row['popcore_id'], row['id']))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return len(found)


def _next_batch(rows):
    """Up to BATCH live entries in book order whose references are accepted or go earlier in this batch."""
    picked, ids = [], set()
    for row in sorted((row for row in rows.values() if row['state'] == 'waiting'), key=lambda row: (ORDER[row['kind']], row['id'])):
        ready = all(row[field] is None or row[field] in ids or (
            row[field] in rows and rows[row[field]]['sent_version'] > 0 and rows[row[field]]['deleted_at'] is None)
            for field in REFS)
        if ready:
            picked.append(row)
            ids.add(row['id'])
            if len(picked) == BATCH:
                break
    return picked


def deletes_held(rows):
    """True while a live entry is not accepted yet (waiting, refused, or behind a refused one). The website's delete
    takes what still hangs under the row, so an entry the book moved out from under a deleted one must have arrived
    there first: no delete goes until every live entry has."""
    return any(row['deleted_at'] is None and row['state'] != 'sent' for row in rows.values())


def _delete_batch(rows):
    """Up to BATCH deletes, lowest level first. A row another row still points at waits: its children's deletes are
    confirmed first, so a child the website refused to delete (a published listing) is never taken with its parent."""
    if deletes_held(rows):
        return []
    pointed_at = {row[field] for row in rows.values() for field in REFS if row[field] is not None}
    return sorted((row for row in rows.values() if row['state'] == 'delete' and row['id'] not in pointed_at),
                  key=lambda row: (-ORDER[row['kind']], row['id']))[:BATCH]


def _drop_unsent(con, rows):
    """Deleted rows the website never had go without sending."""
    ids = [row['id'] for row in rows.values() if row['state'] == 'drop']
    if not ids:
        return 0
    _begin(con)
    try:
        for one in ids:
            con.execute('DELETE FROM dex_entries WHERE id=? AND deleted_at IS NOT NULL AND website_id IS NULL AND sent_version=0',
                        (one,))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return len(ids)


def send(con, cfg, batch, quiet):
    """One call. Returns how many entries the answer settled (ok, refused or error)."""
    memo = {}
    shapes = [entry(con, row['id'], memo) for row in batch]
    answer, _ = call(cfg, 'POST', '/book', body={'quiet': bool(quiet), 'entries': shapes})
    results = answer.get('results') if isinstance(answer, dict) else None
    if not isinstance(results, list):
        raise WebsiteError('The website’s answer to the book could not be read.', 'website_answer')
    by_ref = {}
    for result in results:
        if isinstance(result, dict) and isinstance(result.get('ref'), int):
            by_ref[result['ref']] = result
    in_batch = {shape['ref'] for shape in shapes}
    ok = {ref for ref, result in by_ref.items() if result.get('status') == 'ok'}
    settled = 0
    _begin(con)
    try:
        for shape in shapes:
            result = by_ref.get(shape['ref'])
            if result is None:
                continue  # no answer for it: it stays waiting
            if result.get('status') == 'ok':
                if shape['deleted']:
                    con.execute('DELETE FROM dex_entries WHERE id=? AND deleted_at IS NOT NULL', (shape['ref'],))
                    settled += 1
                    continue
                website_id = result.get('website_id')
                website_id = website_id if isinstance(website_id, int) and not isinstance(website_id, bool) else None
                con.execute('SAVEPOINT dex_ok')
                try:
                    con.execute("""UPDATE dex_entries SET sent_version=MAX(sent_version, ?), send_error=NULL,
                                       website_item=CASE WHEN website_id IS NULL AND ? IS NOT NULL THEN ? ELSE website_item END,
                                       website_id=COALESCE(?, website_id)
                                   WHERE id=?""",
                                (shape['version'], website_id, int(shape['item'] and shape['kind'] == 'product'),
                                 website_id, shape['ref']))
                    con.execute('RELEASE dex_ok')
                except Exception as exc:  # the number is already held by another entry of the kind
                    con.execute('ROLLBACK TO dex_ok')
                    con.execute('RELEASE dex_ok')
                    _mark(con, shape, 'website_number_taken',
                          f'The website gave number {website_id}, which another book entry already holds ({type(exc).__name__}).')
                settled += 1
                continue
            code = str(result.get('code') or result.get('status') or 'error')[:80]
            if code == 'parent_unknown' and any(shape.get(field) in in_batch and shape.get(field) not in ok
                                                for field in ('parent_ref', 'partner_ref', 'character_ref', 'ip_ref')):
                continue  # what it points at was refused in this same batch: it waits behind that, unmarked
            _mark(con, shape, code, str(result.get('message') or 'The website did not take it.')[:500],
                  result.get('status') if result.get('status') in ('refused', 'error') else 'error')
            settled += 1
        con.commit()
    except Exception:
        con.rollback()
        raise
    return settled


def _mark(con, shape, code, message, status='error'):
    con.execute('UPDATE dex_entries SET send_error=? WHERE id=?',
                (json.dumps({'status': status, 'code': code, 'message': message, 'version': shape['version'], 'at': _now()},
                            ensure_ascii=False), shape['ref']))


def run_once(con, cfg, clock=time.monotonic):
    """One pass while the switch is on. Raises WebsiteError when a call fails (nothing from that call is marked)."""
    started = clock()
    notice(con)
    sent = 0
    while clock() - started < PASS_SECONDS:
        rows = classify(con)
        batch = _next_batch(rows)
        if not batch:
            _drop_unsent(con, rows)
            batch = _delete_batch(classify(con))
        if not batch:
            # A held product is still part of the first full send: it goes quiet too once it has its format.
            if state(con, 'first_send_done') is None and not any(row['state'] == 'held' for row in rows.values()):
                set_state(con, 'first_send_done', _now())
                con.commit()
            break
        settled = send(con, cfg, batch, quiet=state(con, 'first_send_done') is None)
        sent += settled
        if not settled:
            break  # the website answered nothing about this batch; try again next pass
    if sent:
        set_state(con, 'last_sent_at', _now())
        set_state(con, 'last_sent_count', sent)
        con.commit()
    return sent


def background_sync():
    """Scheduler job, every 30 seconds. With the website settings missing, or the switch off, it returns at once: no
    call and no write. A failure is kept as the last error and never reaches the stock sync."""
    cfg, _ = settings()
    if cfg is None:
        return
    con = website_sync.connect()
    try:
        if not enabled(con):
            return
        try:
            run_once(con, cfg)
            set_state(con, 'last_error', None)
        except WebsiteError as exc:
            if con.in_transaction:
                con.rollback()
            set_state(con, 'last_error', json.dumps({'code': exc.code, 'message': str(exc), 'at': _now()}, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001 — kept as the last error and logged, never raised into the scheduler
            if con.in_transaction:
                con.rollback()
            log.exception('Dex sync failed')
            set_state(con, 'last_error', json.dumps({'code': 'dex_sync_failed', 'message':
                                                     f'POPCORE could not finish a Dex sync pass ({type(exc).__name__}).',
                                                     'at': _now()}))
        _listings_pass(con, cfg)  # step 4: approved listings, after the book (their book_ref must be on the website)
        set_state(con, 'last_run', _now())
        con.commit()
    finally:
        con.close()


# ── The switch, retry and pictures ───────────────────────────────────────────

def check_website(cfg):
    """Ask the website's book route for one entry. Raises WebsiteError with a plain message when it is not there."""
    call(cfg, 'GET', '/book', params={'page': 1, 'per_page': 1})


def switch(con, cfg, on, actor_sub, confirm_deletes=None):
    if on:
        if cfg is None:
            raise WebsiteError('No website key is set on the server.', 'not_configured')
        if not enabled(con):
            deletes = preview(con)['delete']
            if deletes and confirm_deletes != deletes:
                raise WebsiteError(f'The first send deletes {deletes} entries on the website. Type {deletes} to confirm.',
                                   'confirm_deletes')
            try:
                check_website(cfg)
            except WebsiteError as exc:
                # Kept, so the screen can say plainly that the website's half is not there; the switch stays off.
                set_state(con, 'website_check', json.dumps({'ok': False, 'code': exc.code, 'message': str(exc), 'at': _now()},
                                                           ensure_ascii=False))
                con.commit()
                raise
            set_state(con, 'website_check', json.dumps({'ok': True, 'at': _now()}))
            set_state(con, 'enabled', '1')
    else:
        set_state(con, 'enabled', None)
    set_state(con, 'switched_by', actor_sub)
    set_state(con, 'switched_at', _now())
    con.commit()


def retry(con, entry_id=None):
    """Send a refused entry (or every refused entry) again on the next pass. Returns how many."""
    rows = classify(con)
    ids = [row['id'] for row in rows.values() if row['state'] == 'refused' and (entry_id is None or row['id'] == entry_id)]
    if entry_id is not None and not ids:
        raise dex_book.DexError('That entry is not refused by the website.', 'not_refused', 409)
    _begin(con)
    try:
        for one in ids:
            con.execute('UPDATE dex_entries SET send_error=NULL WHERE id=?', (one,))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return len(ids)


def upload_picture(cfg, entry_id, content, filename, mimetype):
    """Pass one picture to the website's pictures route (contract section 6); returns (picture_id, url, thumb_url)."""
    answer, _ = call(cfg, 'POST', '/pictures', files={'file': (filename, content, mimetype)}, data={'ref': str(entry_id)})
    picture_id = answer.get('picture_id') if isinstance(answer, dict) else None
    if isinstance(picture_id, bool) or not isinstance(picture_id, int) or picture_id <= 0:
        raise WebsiteError('The website’s answer to the picture could not be read.', 'website_answer')
    url, thumb = answer.get('url'), answer.get('thumb_url')
    return picture_id, url if isinstance(url, str) else None, thumb if isinstance(thumb, str) else None


# ── Step 4: listings for stocked items with no listing (contract section 7) ──
#
# The catch-up (contract section 10, step 4): a preview list of POPCORE items with stock a web order may take whose
# POPCORE ID (or, for a blind box, whose Whole Set ID) is on no website item in the stock sync's last catalogue read.
# A manager approves it; nothing goes out by itself, and nothing goes out while the Dex switch is off. An approved item
# goes once its book entry was accepted by the website (book_ref), in calls of up to 50 on the Dex sync's pass. The
# website makes a draft by its own rules and answers each item: created, completed, exists, or refused / error with a
# code. A refused item is listed for a manager and goes again only when what POPCORE would send changes or a manager
# retries. After a created or completed answer the stock sync is asked for its next catalogue read, which brings the
# draft its stock, price and pickup stores (contract 7.7).

LISTING_KINDS = {'random_box': 'blind_box', 'ordinary': 'ordinary', 'confirmed_design': 'confirmed_design'}
LISTING_ORDER = {'blind_box': 0, 'ordinary': 1, 'confirmed_design': 2}  # a design's box goes before it
LISTING_DONE = ('created', 'completed', 'exists')
LISTING_CODES = ('item_malformed', 'book_unknown', 'id_in_use', 'listing_not_variable', 'options_unusual',
                 'listing_failed')
LISTING_LIST = 500


def _book_kind(listing_kind):
    """The book entry a listing's book_ref names: a design for a confirmed design, else a product."""
    return 'design' if listing_kind == 'confirmed_design' else 'product'


def _starting_name(con, product, entry, memo):
    """The listing's starting name (owner, 12 Nov 2026: "a listing's name starts from POPCORE and is then the
    website's"): the book entry's full name, the name shoppers and staff see (contract section 2); POPCORE's own name
    when the book gives none. Sent once only: an answered item is never sent again."""
    if entry is not None:
        name = dex_book.full_name(entry['kind'], entry['name'], dex_book._ancestors(con, entry['parent_id'], memo))
        if name and name.strip():
            return name.strip()[:200]
    return (product['name_cn_en'] or product['jizhanming'] or product['sku']).strip()[:200]


def _whole_set(product, by_sku):
    """(set product row, boxes per set or None) for a blind box with a Whole Set in POPCORE, else (None, None).
    set_id is always exactly <ID>-SET (contract section 7). Its size is the one the stock sync prices it by: the set's
    boxes per set, else its box's (website_sync._wanted_price), so the draft starts at the price the sync then keeps."""
    found = by_sku.get(f"{product['sku']}-SET".upper())
    if found is None or found['stock_form'] != 'sealed_set':
        return None, None
    try:
        boxes = int(found['boxes_per_dan'] or product['boxes_per_dan'] or 0)
    except (TypeError, ValueError):
        boxes = 0
    return found, boxes if boxes >= 1 else None


def listing_item(con, product, entry, by_sku, confirmed, memo=None):
    """One item as contract section 7 sends it. product: its products row; entry: its live book entry (book_ref).
    price_cents is the regular price the stock sync gives the item (a verified POPCORE item); for a design only a price
    a manager set (design_prices.confirmed), never the box's price."""
    memo = {} if memo is None else memo
    kind = LISTING_KINDS[product['stock_form']]
    if kind == 'confirmed_design':
        price = (confirmed.get(product['id']) or {}).get('cents')
    else:
        price = design_prices.cents(product['price']) if product['identity_status'] == 'verified' else None
    item = {'popcore_id': product['sku'], 'kind': kind, 'name': _starting_name(con, product, entry, memo),
            'book_ref': entry['id'] if entry is not None else None,
            'popcore_type': (product['product_type'] or '').strip() or None, 'price_cents': price}
    if kind == 'blind_box':
        found, boxes = _whole_set(product, by_sku)
        item.update(set_id=f"{product['sku']}-SET" if found is not None else None,
                    boxes_per_set=boxes if found is not None else None,
                    set_price_cents=boxes * price if found is not None and boxes and price else None)
    return item


def _catalogue(con):
    """What the listings work reads: products by id and by ID, live book entries by item, the IDs on a website item in
    the last catalogue read (trash left out, as the website does), and the approved rows."""
    products = {row['id']: row for row in con.execute(
        """SELECT id, sku, name_cn_en, jizhanming, price, product_type, stock_form, identity_status, boxes_per_dan
           FROM products WHERE sku IS NOT NULL AND TRIM(sku) != ''""")}
    by_sku = {row['sku'].upper(): row for row in products.values()}
    entries = {row['product_id']: row for row in con.execute(
        """SELECT id, kind, name, parent_id, product_id, sent_version FROM dex_entries
           WHERE deleted_at IS NULL AND product_id IS NOT NULL""")}
    listed = {row[0].upper() for row in con.execute(
        "SELECT popcore_id FROM website_items WHERE kind != 'variable' AND popcore_id != ''")}
    rows = {row['product_id']: dict(row) for row in con.execute('SELECT * FROM dex_listings')}
    return products, by_sku, entries, listed, rows


def _entry_for(entries, product):
    """The item's live book entry when it is of the kind its listing names, else None."""
    entry = entries.get(product['id'])
    kind = LISTING_KINDS.get(product['stock_form'])
    return entry if entry is not None and kind and entry['kind'] == _book_kind(kind) else None


def _accepted(entry):
    """The website knows the entry by its ref: it took a version of it (contract 7: asked only after that)."""
    return entry is not None and entry['sent_version'] > 0


def _listing_row(row, products):
    product = products.get(row['product_id'])
    return {'product_id': row['product_id'], 'popcore_id': product['sku'] if product else row['popcore_id'],
            'state': row['state'], 'approved_by': row['approved_by'], 'approved_at': row['approved_at'],
            'sent_at': row['sent_at'], 'answered_at': row['answered_at'],
            'website_product_id': row['website_product_id'], 'code': row['code'], 'message': row['message'],
            'name': (json.loads(row['sent_item']).get('name') if row['sent_item'] else None)
            or (product and (product['name_cn_en'] or product['jizhanming'])) or row['popcore_id']}


def listings(con):
    """The catch-up preview (read only): stocked items with no listing, each `ready` (its book entry is on the
    website), `book_waiting` (in the book, not taken by the website yet: approvable, it waits) or `not_in_book` (no
    book entry of its kind links it: link it in the book first); then the approved items, the refused ones and the
    answered ones."""
    read_at = website_sync.state(con, 'refreshed_at')
    products, by_sku, entries, listed, rows = _catalogue(con)
    confirmed = design_prices.confirmed(con)
    stock = website_sync._shelf_stock(con)[0] if read_at else {}
    memo, candidates = {}, []
    for product in sorted(products.values(), key=lambda row: (LISTING_ORDER.get(LISTING_KINDS.get(row['stock_form']), 9),
                                                               row['sku'].upper())):
        kind = LISTING_KINDS.get(product['stock_form'])
        if kind is None:
            continue
        units = sum(stock.get(product['id'], {}).values())
        found, _ = _whole_set(product, by_sku) if kind == 'blind_box' else (None, None)
        set_units = sum(stock.get(found['id'], {}).values()) if found is not None else 0
        if units + set_units <= 0:
            continue
        ids = [product['sku']] + ([f"{product['sku']}-SET"] if found is not None else [])
        missing = [one for one in ids if one.upper() not in listed]
        if not missing:
            continue
        row = rows.get(product['id'])
        if row is not None and (row['state'] not in LISTING_DONE or (row['answered_at'] or '') >= (read_at or '')):
            continue  # approved, refused, or answered since the last catalogue read: listed below
        entry = _entry_for(entries, product)
        item = listing_item(con, product, entry, by_sku, confirmed, memo)
        candidates.append({
            **item, 'product_id': product['id'], 'stock': units, 'set_stock': set_units if found is not None else None,
            'missing': missing, 'action': 'complete' if product['sku'].upper() in listed else 'new',
            'state': 'ready' if _accepted(entry) else 'book_waiting' if entry is not None else 'not_in_book',
            'entry': None if entry is None else {'id': entry['id'], 'kind': entry['kind'], 'name': entry['name']},
            'again': row is not None})
    answered = [_listing_row(row, products) for row in rows.values()]
    approved = []
    for row in sorted((one for one in answered if one['state'] == 'approved'), key=lambda one: one['popcore_id'].upper()):
        product = products.get(row['product_id'])
        entry = _entry_for(entries, product) if product else None
        row['waits'] = None if _accepted(entry) else 'book' if entry is not None else 'not_in_book'
        approved.append(row)
    refused = sorted((one for one in answered if one['state'] in ('refused', 'error')),
                     key=lambda one: one['answered_at'] or '', reverse=True)
    done = sorted((one for one in answered if one['state'] in LISTING_DONE),
                  key=lambda one: one['answered_at'] or '', reverse=True)
    error = state(con, 'listings_last_error')
    counts = {'candidates': len(candidates), **{key: sum(1 for one in candidates if one['state'] == key)
                                               for key in ('ready', 'book_waiting', 'not_in_book')},
              'approvable': sum(1 for one in candidates if one['state'] != 'not_in_book'),
              'new': sum(1 for one in candidates if one['action'] == 'new'),
              'complete': sum(1 for one in candidates if one['action'] == 'complete'),
              'approved': len(approved), 'refused': len(refused), 'done': len(done),
              **{key: sum(1 for one in done if one['state'] == key) for key in LISTING_DONE}}
    return {'enabled': enabled(con), 'catalogue_read_at': read_at, 'counts': counts,
            'candidates': candidates[:LISTING_LIST], 'approved': approved[:LISTING_LIST], 'refused': refused[:LISTING_LIST],
            'done': done[:100], 'last_error': json.loads(error) if error else None}


def approve_listings(con, product_ids, actor_sub, count=None):
    """A manager approves items from the preview list (product_ids), or every approvable one (product_ids None, with
    count: how many the screen showed, so a list that changed meanwhile is not approved unseen)."""
    found = listings(con)
    approvable = {one['product_id']: one for one in found['candidates'] if one['state'] != 'not_in_book'}
    if product_ids is None:
        if count != len(approvable):
            raise dex_book.DexError(f'The list changed: {len(approvable)} can be approved now. Look again.',
                                    'list_changed', 409)
        product_ids = list(approvable)
    elif not product_ids or any(one not in approvable for one in product_ids):
        raise dex_book.DexError('That item is not on the list to approve: it has a listing, no stock, is approved '
                                'already, or is not in the book.', 'not_listable', 409)
    now = _now()
    _begin(con)
    try:
        for one in product_ids:
            con.execute("""INSERT INTO dex_listings(product_id, popcore_id, state, approved_by, approved_at)
                           VALUES (?, ?, 'approved', ?, ?)
                           ON CONFLICT(product_id) DO UPDATE SET popcore_id=excluded.popcore_id, state='approved',
                               approved_by=excluded.approved_by, approved_at=excluded.approved_at, sent_item=NULL,
                               sent_at=NULL, answered_at=NULL, website_product_id=NULL, code=NULL, message=NULL""",
                        (one, approvable[one]['popcore_id'], actor_sub or '', now))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return {'approved': len(product_ids)}


def _change_listing(con, product_id, states, sql, missing):
    _begin(con)
    try:
        changed = con.execute(f"UPDATE dex_listings SET {sql} WHERE product_id=? AND state IN ({','.join('?' * len(states))})"
                              if sql else
                              f"DELETE FROM dex_listings WHERE product_id=? AND state IN ({','.join('?' * len(states))})",
                              (product_id, *states)).rowcount
        con.commit()
    except Exception:
        con.rollback()
        raise
    if not changed:
        raise dex_book.DexError(missing, 'listing_not_found', 409)
    return changed


def withdraw_listing(con, product_id):
    """Take an approved (not yet answered) or refused item off the list; it shows in the preview again."""
    return {'withdrawn': _change_listing(con, product_id, ('approved', 'refused', 'error'), None,
                                         'Only an approved or refused item can be withdrawn.')}


def retry_listings(con, product_id=None):
    """A refused item (or every refused item) goes again with the next pass."""
    if product_id is not None:
        return {'retried': _change_listing(con, product_id, ('refused', 'error'), "state='approved', code=NULL, message=NULL",
                                           'That item is not refused by the website.')}
    _begin(con)
    try:
        changed = con.execute("""UPDATE dex_listings SET state='approved', code=NULL, message=NULL
                                 WHERE state IN ('refused', 'error')""").rowcount
        con.commit()
    except Exception:
        con.rollback()
        raise
    return {'retried': changed}


def _listing_batch(con):
    """Up to BATCH approved items whose book entry the website has, boxes first: (row, item) pairs. A refused item is
    picked again only when what POPCORE would send for it changed (contract section 8). Read only."""
    if con.execute("SELECT 1 FROM dex_listings WHERE state IN ('approved', 'refused', 'error') LIMIT 1").fetchone() is None:
        return []  # nothing approved: one small read a pass
    products, by_sku, entries, _, rows = _catalogue(con)
    confirmed, memo, picked = design_prices.confirmed(con), {}, []
    for row in rows.values():
        if row['state'] not in ('approved', 'refused', 'error'):
            continue
        product = products.get(row['product_id'])
        if product is None or product['stock_form'] not in LISTING_KINDS:
            continue
        entry = _entry_for(entries, product)
        if not _accepted(entry):
            continue  # waits until the book send has it
        item = listing_item(con, product, entry, by_sku, confirmed, memo)
        if row['state'] != 'approved' and row['sent_item'] == json.dumps(item, ensure_ascii=False, sort_keys=True):
            continue
        picked.append((row, item))
    picked.sort(key=lambda pair: (LISTING_ORDER[pair[1]['kind']], pair[1]['popcore_id'].upper()))
    return picked[:BATCH]


def _whole_number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def send_listings_batch(con, cfg, batch):
    """One POST /listings call. Returns how many items the answer settled. A call that fails marks nothing."""
    items = [item for _, item in batch]
    answer, _ = call(cfg, 'POST', '/listings', body={'items': items})
    results = answer.get('results') if isinstance(answer, dict) else None
    if not isinstance(results, list):
        raise WebsiteError('The website’s answer to the listings could not be read.', 'website_answer')
    by_id = {}
    for result in results:
        if isinstance(result, dict) and isinstance(result.get('popcore_id'), str):
            by_id.setdefault(result['popcore_id'].strip().upper(), result)
    if len(results) == len(items):  # an item whose ID the website could not read answers with none: by its place
        for item, result in zip(items, results):
            if isinstance(result, dict) and result.get('popcore_id') is None:
                by_id.setdefault(item['popcore_id'].upper(), result)
    now, settled, refresh = _now(), 0, False
    _begin(con)
    try:
        for row, item in batch:
            result = by_id.get(item['popcore_id'].upper())
            if result is None:
                continue  # no answer for it: it stays as it was and goes again
            status, code = result.get('status'), result.get('code')
            message = result.get('message') if isinstance(result.get('message'), str) else None
            if status in LISTING_DONE:
                code = None
                refresh = refresh or status in ('created', 'completed')
            elif status in ('refused', 'error'):
                code = str(code or ('listing_failed' if status == 'error' else 'refused'))[:80]
                message = message or 'The website did not make it.'
            else:
                message = f'The website answered “{str(status)[:40]}” for it, which POPCORE cannot read.'
                status, code = 'error', 'website_answer'
            con.execute("""INSERT INTO dex_listings(product_id, popcore_id, state, approved_by, approved_at, sent_item,
                               sent_at, answered_at, website_product_id, code, message)
                           VALUES (:product_id, :popcore_id, :state, :approved_by, :approved_at, :sent_item, :at, :at,
                               :website_product_id, :code, :message)
                           ON CONFLICT(product_id) DO UPDATE SET popcore_id=excluded.popcore_id, state=excluded.state,
                               sent_item=excluded.sent_item, sent_at=excluded.sent_at, answered_at=excluded.answered_at,
                               website_product_id=excluded.website_product_id, code=excluded.code,
                               message=excluded.message""",
                        {'product_id': row['product_id'], 'popcore_id': item['popcore_id'], 'state': status,
                         'approved_by': row['approved_by'], 'approved_at': row['approved_at'],
                         'sent_item': json.dumps(item, ensure_ascii=False, sort_keys=True), 'at': now,
                         'website_product_id': _whole_number(result.get('product_id')), 'code': code,
                         'message': message[:500] if message else None})
            settled += 1
        if refresh:  # the draft gets its stock, price and pickup stores from the stock sync's next read (contract 7.7)
            website_sync.set_state(con, 'refresh_requested', '1')
        con.commit()
    except Exception:
        con.rollback()
        raise
    return settled


def send_listings(con, cfg, clock=time.monotonic):
    """The listings part of a pass, while the switch is on. Raises WebsiteError when a call fails."""
    started, settled = clock(), 0
    while clock() - started < PASS_SECONDS:
        batch = _listing_batch(con)
        if not batch:
            break
        done = send_listings_batch(con, cfg, batch)
        settled += done
        if not done:
            break  # the website answered nothing about this call; try again next pass
    return settled


def _listings_pass(con, cfg):
    """send_listings with its failure kept apart from the book's (listings_last_error); never raised."""
    error = None
    try:
        send_listings(con, cfg)
    except WebsiteError as exc:
        error = {'code': exc.code, 'message': str(exc), 'at': _now()}
    except Exception as exc:  # noqa: BLE001 — kept and logged, never raised into the scheduler
        log.exception('Dex listings failed')
        error = {'code': 'dex_listings_failed', 'message': f'POPCORE could not finish the listings ({type(exc).__name__}).',
                 'at': _now()}
    if error is not None and con.in_transaction:
        con.rollback()
    if error is not None or state(con, 'listings_last_error') is not None:
        set_state(con, 'listings_last_error', json.dumps(error, ensure_ascii=False) if error else None)
