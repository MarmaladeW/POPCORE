"""图鉴 Dex sync, step 3: POPCORE sends the book to popcore.ca (owner, 2026-11-14: "yes build the POPCORE -> popcore.ca
sync"), behind its own switch, off until an admin turns it on. The contract is docs/dex-book-contract.md (1.7).

- What needs sending: an entry whose version is above its sent_version. Every entry starts at 0, so the first run
  sends the whole book. A new price recorded for a design's item (design_prices) or a new POPCORE ID on a linked item
  is noticed here and raises the entry's version, since neither raises it by itself (owner, 2026-11-14: "the price
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
- It reads website_sync.config() for the website's address and key and makes its own calls. It never touches stock,
  prices, orders or the stock sync's state.
"""
import json
import logging
import time
from datetime import datetime, timezone

import requests

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
HALF_MISSING = ('The website’s half of the book sync is not there yet: popcore.ca does not answer its book route. '
                'Nothing was sent.')


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
    POST /book counts only HTTP 200 (contract section 3); the others any 2xx."""
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
        raise WebsiteError(HALF_MISSING, 'website_half_missing', 404)
    if status in (401, 403):
        raise WebsiteError('The website refused POPCORE’s key on its book route. Its half of the book sync may not be '
                           'there yet, or the key is not allowed on it.', 'website_auth', status)
    if not (status == 200 if (method, path) == ('POST', '/book') else 200 <= status < 300):
        message = answer.get('message') if isinstance(answer, dict) else None
        raise WebsiteError(str(message or f'The website answered HTTP {status}.')[:300], 'website_error', status)
    return answer, response.headers


# ── Reading the book's sync facts ────────────────────────────────────────────

_ROWS = """SELECT e.id, e.kind, e.name, e.parent_id, e.partner_id, e.character_id, e.ip_id, e.version, e.sent_version,
                  e.send_error, e.deleted_at, e.website_id, e.website_item, e.sync_price_id, e.sync_popcore_id,
                  p.sku AS popcore_id,
                  CASE WHEN e.kind='design' AND e.product_id IS NOT NULL THEN
                      (SELECT MAX(d.id) FROM design_prices d WHERE d.product_id=e.product_id) END AS price_id
           FROM dex_entries e LEFT JOIN products p ON p.id=e.product_id"""


def _error(row):
    try:
        found = json.loads(row['send_error']) if row['send_error'] else None
    except ValueError:
        found = None
    return found if isinstance(found, dict) else None


def classify(con):
    """{id: facts} for every row, each with `state`:
    sent (the website has this version), waiting, refused (at this version), behind (waits behind a refused entry),
    delete (a deleted row to send), drop (a deleted row the website never had: removed without sending)."""
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
            row['state'] = 'refused' if refused else 'waiting' if due else 'sent'

    def blocked(row, seen):
        """True when something this entry points at was refused, or waits behind a refused entry."""
        for field in REFS:
            dep = rows.get(row[field])
            if dep is None or dep['id'] in seen:
                continue
            if dep['sent_version'] > 0 and dep['deleted_at'] is None:
                continue  # the website knows it
            if dep['state'] == 'refused' or blocked(dep, seen | {dep['id']}):
                return True
        return False
    for row in rows.values():
        if row['state'] == 'waiting' and blocked(row, {row['id']}):
            row['state'] = 'behind'
    return rows


def _counts(rows):
    counts = {'waiting': 0, 'sent': 0, 'refused': 0, 'behind': 0, 'deletes': 0}
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
        'counts': _counts(rows), 'refused': _refused_list(con, rows),
        # Deletes that wait because a live entry was refused (not merely because the rest has not gone yet).
        'deletes_held': (any(row['state'] == 'delete' for row in rows.values()) and any(
            row['deleted_at'] is None and row['state'] in ('refused', 'behind') for row in rows.values())),
    }


def preview(con):
    """What the next full send does: entries the website will create, update and delete, and the deletes by name with
    their place in the book."""
    rows = classify(con)
    live = [row for row in rows.values() if row['state'] in ('waiting', 'behind', 'refused') and row['deleted_at'] is None]
    create = sum(1 for row in live if row['website_id'] is None and row['sent_version'] == 0)
    deletes = sorted((row for row in rows.values() if row['state'] in ('delete', 'refused') and row['deleted_at']),
                     key=lambda row: (-ORDER[row['kind']], row['id']))
    return {'create': create, 'update': len(live) - create, 'delete': len(deletes),
            'removed_quietly': sum(1 for row in rows.values() if row['state'] == 'drop'),
            'deletes': [{'id': row['id'], 'kind': row['kind'], 'name': row['name'], 'website_id': row['website_id'],
                         'place': _place(rows, row)} for row in deletes]}


def entry_state(con, entry_id):
    rows = classify(con)
    row = rows.get(entry_id)
    if row is None or row['deleted_at'] is not None:
        raise dex_book.DexError('That Dex entry does not exist.', 'dex_not_found', 404)
    return {'enabled': enabled(con), 'state': row['state'], 'version': row['version'], 'sent_version': row['sent_version'],
            'website_id': row['website_id'], 'code': row['error'].get('code') if row['state'] == 'refused' else None,
            'message': row['error'].get('message') if row['state'] == 'refused' else None}


# ── What is sent ─────────────────────────────────────────────────────────────

def _price_history(con, product_id):
    """Every price recorded for the item, oldest first (contract 1.5). Only SELECTs from design_prices."""
    return [{'price': row['cents'] / 100,
             'recorded_at': row['set_at'].replace(' ', 'T') + ('' if row['set_at'].endswith('Z') else 'Z')}
            for row in con.execute('SELECT cents, set_at FROM design_prices WHERE product_id=? ORDER BY id', (product_id,))]


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
    if kind == 'design':  # always whole; empty when the shop has never priced it, so the chart shows only POPCORE's
        extras['price_history'] = _price_history(con, row['product_id']) if row['product_id'] is not None else []
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
    """A new recorded price for a design's item, or a new POPCORE ID on a linked item: raise the version, so the entry
    is sent again (neither raises it by itself)."""
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
            if state(con, 'first_send_done') is None:
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
