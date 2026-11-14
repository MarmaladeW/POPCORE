"""图鉴 Dex: the book lives in POPCORE (owner, 2026-11-12: "The Dex itself lives on POPCORE").

One table, dex_entries, holds every level: companies and partners at the top, then IPs, characters,
series, products and designs. This module is the only place that writes it. It never touches products
or anything the till reads; a book product or design only points at a POPCORE item by product_id.

Rules (docs/dex-book.md):
- Which kind may sit under which: PARENTS.
- A name is required; two live entries of one kind under one parent cannot share a name (any case).
- partner_id on series and products, a live partner; character_id on series, products and designs, a live
  character; blind_box on products; rarity on designs (the website's words).
- ip_id (contract 1.3, owner 2026-11-13): on a product or design with no IP above it (a series with several IPs
  stays straight under its company), the live IP it belongs to, so it also shows on that IP's page. Refused when
  an IP sits above: its IP is the one above. Moving a parent does not re-check what sits under it.
- product_id: a book product links an ordinary POPCORE item or a blind box (not a confirmed design or a
  sealed set); a book design links a confirmed design. One POPCORE item, one live entry.
- extras: the refined copy, only the keys the contract names for the kind (docs/dex-book-contract.md §4).
- Every accepted change raises version by one.
- Delete takes the entry and everything under it. It is refused while any of them is linked to a POPCORE
  item, or a live entry names one as its partner, character or IP. Rows the website has (website_id) are kept
  with deleted_at, so a later step can tell the website; the others go. Deleted rows are never read.
- batch(con): inside a transaction the caller began, each write is a savepoint instead of a commit, so many writes,
  each with every rule, commit or roll back as one (dex_decisions applies the owner's review this way).
"""
import json
import sqlite3
from contextlib import contextmanager


class DexError(Exception):
    def __init__(self, message, code, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


KINDS = ('company', 'partner', 'ip', 'character', 'series', 'product', 'design')
PARENTS = {
    'company': (), 'partner': (),
    'ip': ('company',),
    'character': ('ip', 'partner'),  # contract 1.2: a partner's own characters (Disney's princesses…)
    'series': ('ip', 'company'),
    'product': ('series', 'ip', 'company'),
    'design': ('product',),
}
CHILDREN = {kind: tuple(child for child in KINDS if kind in PARENTS[child]) for kind in KINDS}
RARITIES = ('normal', 'popular', 'secret', 'super_secret', 'special')
EXTRAS = {
    'company': (), 'partner': (),
    'ip': ('description', 'aliases', 'is_original'),
    'character': ('description', 'aliases'),
    'series': ('release_date', 'flavour_text', 'description', 'aliases', 'tags', 'designer'),
    'product': ('format', 'release_label', 'description', 'retail_price', 'retail_price_box', 'retail_currency',
                'variants_normal', 'variants_secret', 'secret_odds', 'parent_category'),
    'design': ('weight', 'weight_range', 'shake_level', 'shake_note', 'flavour_text', 'market_prices'),
}
PARTNER_KINDS = ('series', 'product')
CHARACTER_KINDS = ('series', 'product', 'design')
IP_KINDS = ('product', 'design')
MAX_NAME = 200
MAX_EXTRAS = 20000
SEARCH_LIMIT = 50
EDITABLE = ('name', 'parent_id', 'partner_id', 'character_id', 'ip_id', 'blind_box', 'rarity', 'product_id', 'extras')

_COLUMNS = """e.id, e.kind, e.parent_id, e.partner_id, e.character_id, e.name, e.sort_order, e.blind_box, e.rarity,
    e.product_id, e.picture_id, e.picture_url, e.thumb_url, e.extras, e.website_id, e.version, e.created_at,
    e.updated_at, e.updated_by, e.ip_id, ni.name AS named_ip_name,
    (SELECT COUNT(*) FROM dex_entries c WHERE c.parent_id=e.id AND c.deleted_at IS NULL) AS child_count,
    p.sku AS product_sku, COALESCE(NULLIF(p.name_cn_en, ''), p.jizhanming) AS product_name"""
_FROM = """FROM dex_entries e LEFT JOIN products p ON p.id=e.product_id
    LEFT JOIN dex_entries ni ON ni.id=e.ip_id AND ni.deleted_at IS NULL WHERE e.deleted_at IS NULL"""


def _shape(row):
    entry = dict(row)
    entry['blind_box'] = None if entry['blind_box'] is None else bool(entry['blind_box'])
    name = entry.pop('named_ip_name')
    entry['named_ip'] = None if entry['ip_id'] is None or name is None else {'id': entry['ip_id'], 'name': name}
    try:
        entry['extras'] = json.loads(entry['extras'] or '{}')
    except ValueError:
        entry['extras'] = {}
    return entry


def _live(con, entry_id):
    row = con.execute(f'SELECT {_COLUMNS} {_FROM} AND e.id=?', (entry_id,)).fetchone()
    return _shape(row) if row else None


def _need(con, entry_id):
    entry = _live(con, entry_id) if isinstance(entry_id, int) and not isinstance(entry_id, bool) else None
    if entry is None:
        raise DexError('That Dex entry does not exist.', 'dex_not_found', 404)
    return entry


def _path(con, entry):
    """The entry's ancestors, top first."""
    path, seen, parent = [], {entry['id']}, entry['parent_id']
    while parent is not None and parent not in seen:
        seen.add(parent)
        row = con.execute('SELECT id, kind, name, parent_id FROM dex_entries WHERE id=? AND deleted_at IS NULL',
                          (parent,)).fetchone()
        if row is None:
            break
        path.insert(0, {'id': row['id'], 'kind': row['kind'], 'name': row['name']})
        parent = row['parent_id']
    return path


def _ip_above(con, parent_id):
    """The first IP met walking up from parent_id (itself included), or None."""
    seen = set()
    while parent_id is not None and parent_id not in seen:
        seen.add(parent_id)
        row = con.execute('SELECT id, kind, name, parent_id FROM dex_entries WHERE id=? AND deleted_at IS NULL',
                          (parent_id,)).fetchone()
        if row is None:
            return None
        if row['kind'] == 'ip':
            return {'id': row['id'], 'kind': 'ip', 'name': row['name']}
        parent_id = row['parent_id']
    return None


def _brief(con, entry_id):
    if entry_id is None:
        return None
    row = con.execute('SELECT id, kind, name FROM dex_entries WHERE id=? AND deleted_at IS NULL', (entry_id,)).fetchone()
    return dict(row) if row else None


# Reads ------------------------------------------------------------------------------------------------

def children(con, parent_id):
    _need(con, parent_id)
    return [_shape(row) for row in con.execute(
        f'SELECT {_COLUMNS} {_FROM} AND e.parent_id=? ORDER BY e.sort_order, e.name COLLATE NOCASE, e.id', (parent_id,))]


def top(con, kind):
    if kind not in ('company', 'partner'):
        raise DexError('Only companies and partners are listed at the top.', 'invalid_input')
    return [_shape(row) for row in con.execute(
        f'SELECT {_COLUMNS} {_FROM} AND e.kind=? AND e.parent_id IS NULL ORDER BY e.sort_order, e.name COLLATE NOCASE, e.id',
        (kind,))]


def ips(con):
    """Every live IP, with its path (its company), for the IP picker."""
    found = []
    for row in con.execute(f'SELECT {_COLUMNS} {_FROM} AND e.kind=? ORDER BY e.name COLLATE NOCASE, e.id', ('ip',)):
        entry = _shape(row)
        entry['path'] = _path(con, entry)
        found.append(entry)
    return found


def named(con, ip_id):
    """The live products and designs elsewhere in the book that name this IP, each with its path."""
    if _need(con, ip_id)['kind'] != 'ip':
        raise DexError('Only an IP is named by other entries.', 'invalid_input')
    found = []
    for row in con.execute(f"""SELECT {_COLUMNS} {_FROM} AND e.ip_id=?
                               ORDER BY e.kind='design', e.name COLLATE NOCASE, e.id""", (ip_id,)):
        entry = _shape(row)
        entry['path'] = _path(con, entry)
        found.append(entry)
    return found


def search(con, text, kinds=None):
    text = (text or '').strip()
    if not text:
        return []
    kinds = [kind for kind in (kinds or []) if kind in KINDS]
    where, params = '', [_like(text)]
    if kinds:
        where = f' AND e.kind IN ({",".join("?" * len(kinds))})'
        params += kinds
    rows = con.execute(f"""SELECT {_COLUMNS} {_FROM} AND e.name LIKE ? ESCAPE '\\'{where}
                           ORDER BY length(e.name), e.name COLLATE NOCASE, e.id LIMIT {SEARCH_LIMIT}""",
                       params).fetchall()
    found = []
    for row in rows:
        entry = _shape(row)
        entry['path'] = _path(con, entry)
        found.append(entry)
    return found


def _like(text):
    return '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


UNLINKED_FORMS = ('random_box', 'ordinary', 'confirmed_design')  # and unsorted (no form yet); never a sealed set
MAX_PAGE = 100


def unlinked(con, text='', page=1, per_page=50):
    """POPCORE items no live book entry links: boxes, ordinary items, unsorted items and confirmed designs. A
    design comes with its box and the book product that box is linked to, if any."""
    per_page = max(1, min(int(per_page), MAX_PAGE))
    page = max(1, int(page))
    where = f"""(p.stock_form IS NULL OR p.stock_form IN ({','.join('?' * len(UNLINKED_FORMS))}))
                AND NOT EXISTS (SELECT 1 FROM dex_entries d WHERE d.product_id=p.id AND d.deleted_at IS NULL)"""
    params = list(UNLINKED_FORMS)
    text = (text or '').strip()
    if text:
        where += " AND (p.sku LIKE ? ESCAPE '\\' OR p.name_cn_en LIKE ? ESCAPE '\\' OR p.jizhanming LIKE ? ESCAPE '\\')"
        params += [_like(text)] * 3
    total = con.execute(f'SELECT COUNT(*) FROM products p WHERE {where}', params).fetchone()[0]
    rows = con.execute(
        f"""SELECT p.id, p.sku, p.name_cn_en, p.jizhanming, p.design_name, p.stock_form, p.ip_series, p.brand,
                   box.id AS box_id, box.sku AS box_sku, COALESCE(NULLIF(box.name_cn_en, ''), box.jizhanming) AS box_name,
                   be.id AS box_entry_id, be.name AS box_entry_name
            FROM products p
            LEFT JOIN product_series ps ON p.stock_form='confirmed_design' AND ps.id=p.series_id
            LEFT JOIN products box ON box.id=ps.product_id
            LEFT JOIN dex_entries be ON be.product_id=box.id AND be.deleted_at IS NULL AND be.kind='product'
            WHERE {where} ORDER BY p.sku COLLATE NOCASE, p.id LIMIT ? OFFSET ?""",
        params + [per_page, (page - 1) * per_page]).fetchall()
    items = []
    for row in rows:
        item = {key: row[key] for key in ('id', 'sku', 'name_cn_en', 'jizhanming', 'design_name', 'stock_form', 'ip_series',
                                          'brand')}
        item['box'] = None if row['box_id'] is None else {'id': row['box_id'], 'sku': row['box_sku'], 'name': row['box_name']}
        item['box_entry'] = None if row['box_entry_id'] is None else {'id': row['box_entry_id'], 'name': row['box_entry_name']}
        items.append(item)
    return {'items': items, 'total': total, 'page': page, 'per_page': per_page}


def detail(con, entry_id):
    entry = _need(con, entry_id)
    entry['path'] = _path(con, entry)
    entry['allowed_children'] = list(CHILDREN[entry['kind']])
    entry['allowed_parents'] = list(PARENTS[entry['kind']])
    entry['product'] = None if entry['product_id'] is None else {
        'id': entry['product_id'], 'sku': entry['product_sku'], 'name': entry['product_name']}
    entry['partner'] = _brief(con, entry['partner_id'])
    entry['character'] = _brief(con, entry['character_id'])
    # The IP the entry sits under (or is): its characters are the ones the editor offers.
    entry['ip'] = ({'id': entry['id'], 'kind': 'ip', 'name': entry['name']} if entry['kind'] == 'ip'
                   else next((step for step in reversed(entry['path']) if step['kind'] == 'ip'), None))
    # Contract 1.3: the products and designs elsewhere (under a company) that name this IP.
    entry['named_by'] = named(con, entry['id']) if entry['kind'] == 'ip' else []
    return entry


# Checks -----------------------------------------------------------------------------------------------

def _id_or_none(value, field):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise DexError(f'{field} must be a number or empty.', 'invalid_input')
    return value


def _check(con, row, entry_id=None):
    """Raise DexError unless the whole row (after the change) keeps every rule."""
    kind = row['kind']
    name = row['name']
    if not isinstance(name, str) or not name.strip():
        raise DexError('A name is required.', 'name_required')
    row['name'] = name = ' '.join(name.split())
    if len(name) > MAX_NAME:
        raise DexError(f'A name is at most {MAX_NAME} characters.', 'invalid_input')

    parent_id = _id_or_none(row['parent_id'], 'parent_id')
    if PARENTS[kind]:
        if parent_id is None:
            raise DexError(f'A {kind} goes under a {" or ".join(PARENTS[kind])}.', 'parent_required')
        parent = _brief(con, parent_id)
        if parent is None:
            raise DexError('That parent does not exist.', 'parent_not_found', 404)
        if parent['kind'] not in PARENTS[kind]:
            raise DexError(f'A {kind} cannot go under a {parent["kind"]}; it goes under a {" or ".join(PARENTS[kind])}.',
                           'parent_kind')
    elif parent_id is not None:
        raise DexError(f'A {kind} sits at the top of the book, with no parent.', 'parent_kind')

    folded = name.casefold()
    for other in con.execute(
            'SELECT id, name FROM dex_entries WHERE kind=? AND parent_id IS ? AND deleted_at IS NULL AND id IS NOT ?',
            (kind, parent_id, entry_id)):
        if other['name'].casefold() == folded:
            raise DexError(f'There is already a {kind} named “{other["name"]}” here.', 'name_taken', 409)

    for field, kinds, target in (('partner_id', PARTNER_KINDS, 'partner'), ('character_id', CHARACTER_KINDS, 'character')):
        value = _id_or_none(row[field], field)
        if value is None:
            continue
        if kind not in kinds:
            raise DexError(f'Only {" and ".join(kinds)} entries have a {target}.', 'field_not_allowed')
        found = _brief(con, value)
        if found is None or found['kind'] != target:
            raise DexError(f'That {target} does not exist.', f'{target}_not_found')

    # Contract 1.3: a product or design with no IP above names its own IP. row.get: the load's rows carry no ip_id.
    ip_id = _id_or_none(row.get('ip_id'), 'ip_id')
    if ip_id is not None:
        if kind not in IP_KINDS:
            raise DexError(f'Only {" and ".join(IP_KINDS)} entries name an IP.', 'field_not_allowed')
        above = _ip_above(con, parent_id)
        if above is not None:
            raise DexError(f'Its IP is the one above ({above["name"]}); leave IP empty.', 'ip_above')
        found = _brief(con, ip_id)
        if found is None or found['kind'] != 'ip':
            raise DexError('That IP does not exist.', 'ip_not_found')

    if row['blind_box'] is not None:
        if kind != 'product':
            raise DexError('Only products are marked blind box.', 'field_not_allowed')
        if not isinstance(row['blind_box'], bool) and row['blind_box'] not in (0, 1):
            raise DexError('blind_box is yes, no or empty.', 'invalid_input')
        row['blind_box'] = int(bool(row['blind_box']))
    if row['rarity'] is not None:
        if kind != 'design':
            raise DexError('Only designs have a rarity.', 'field_not_allowed')
        if row['rarity'] not in RARITIES:
            raise DexError(f'Rarity is one of {", ".join(RARITIES)}.', 'invalid_input')

    product_id = _id_or_none(row['product_id'], 'product_id')
    if product_id is not None:
        if kind not in ('product', 'design'):
            raise DexError('Only products and designs link to a POPCORE item.', 'field_not_allowed')
        item = con.execute('SELECT id, sku, stock_form FROM products WHERE id=?', (product_id,)).fetchone()
        if item is None:
            raise DexError('That POPCORE item does not exist.', 'product_not_found', 404)
        if kind == 'design' and item['stock_form'] != 'confirmed_design':
            raise DexError('A design links only a confirmed design (确认款).', 'product_form')
        if kind == 'product' and item['stock_form'] in ('confirmed_design', 'sealed_set'):
            raise DexError('A product cannot link a confirmed design or a sealed set; link its box.', 'product_form')
        taken = con.execute('SELECT id, name FROM dex_entries WHERE product_id=? AND deleted_at IS NULL AND id IS NOT ?',
                            (product_id, entry_id)).fetchone()
        if taken:
            raise DexError(f'{item["sku"]} is already linked to “{taken["name"]}”.', 'product_taken', 409)

    extras = row['extras']
    if not isinstance(extras, dict):
        raise DexError('extras is an object of named values.', 'invalid_input')
    unknown = sorted(set(extras) - set(EXTRAS[kind]))
    if unknown:
        raise DexError(f'A {kind} has no {", ".join(unknown)}.', 'extras_unknown')
    if len(json.dumps(extras, ensure_ascii=False)) > MAX_EXTRAS:
        raise DexError('Those details are too long.', 'invalid_input')


def _actor(actor):
    return (actor or {}).get('sub')


_BATCHES = set()  # connections inside one caller-held transaction (dex_decisions): each write is a savepoint


@contextmanager
def batch(con):
    """Inside this block create, update, delete and reorder do not commit: each is a savepoint inside the
    transaction the caller has already begun (BEGIN IMMEDIATE), so many writes, each with every rule, commit or
    roll back as one. The caller commits or rolls back."""
    if not con.in_transaction:
        raise RuntimeError('dex_book.batch needs a transaction begun by the caller')
    _BATCHES.add(id(con))
    try:
        yield con
    finally:
        _BATCHES.discard(id(con))


def _write_in_batch(con, work):
    con.execute('SAVEPOINT dex_write')
    try:
        result = work()
    except sqlite3.IntegrityError as exc:
        con.execute('ROLLBACK TO dex_write')
        con.execute('RELEASE dex_write')
        raise DexError('That clashes with another entry; reload and try again.', 'dex_conflict', 409) from exc
    except Exception:
        con.execute('ROLLBACK TO dex_write')
        con.execute('RELEASE dex_write')
        raise
    con.execute('RELEASE dex_write')
    return result


def _write(con, work):
    """One write transaction; the name and link rules hold against another write at the same moment."""
    if id(con) in _BATCHES:
        return _write_in_batch(con, work)
    if con.in_transaction:
        con.commit()
    con.execute('BEGIN IMMEDIATE')
    try:
        result = work()
        con.commit()
        return result
    except sqlite3.IntegrityError as exc:
        con.rollback()
        raise DexError('That clashes with another entry; reload and try again.', 'dex_conflict', 409) from exc
    except Exception:
        con.rollback()
        raise


def _next_order(con, parent_id, kind):
    row = con.execute('SELECT MAX(sort_order) FROM dex_entries WHERE parent_id IS ? AND kind=? AND deleted_at IS NULL',
                      (parent_id, kind)).fetchone()
    return (row[0] or 0) + 10


def _body(data):
    if not isinstance(data, dict):
        raise DexError('Expected a JSON object.', 'invalid_input')
    return data


# Writes -----------------------------------------------------------------------------------------------

def create(con, data, actor):
    data = _body(data)
    kind = data.get('kind')
    if kind not in KINDS:
        raise DexError(f'kind is one of {", ".join(KINDS)}.', 'invalid_input')
    unknown = sorted(set(data) - set(EDITABLE) - {'kind'})
    if unknown:
        raise DexError(f'Unknown fields: {", ".join(unknown)}.', 'invalid_input')
    row = {field: data.get(field) for field in EDITABLE}
    row['kind'] = kind
    row['extras'] = {} if data.get('extras') is None else data['extras']

    def work():
        _check(con, row)
        cur = con.execute(
            """INSERT INTO dex_entries(kind, parent_id, partner_id, character_id, ip_id, name, sort_order, blind_box,
                                       rarity, product_id, extras, updated_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (kind, row['parent_id'], row['partner_id'], row['character_id'], row['ip_id'], row['name'],
             _next_order(con, row['parent_id'], kind), row['blind_box'], row['rarity'], row['product_id'],
             json.dumps(row['extras'], ensure_ascii=False), _actor(actor)))
        return cur.lastrowid
    return detail(con, _write(con, work))


def update(con, entry_id, data, actor):
    data = _body(data)
    unknown = sorted(set(data) - set(EDITABLE))
    if unknown:
        raise DexError(f'Unknown fields: {", ".join(unknown)}.', 'invalid_input')

    def work():
        entry = _need(con, entry_id)
        row = {field: entry[field] for field in EDITABLE}
        row['kind'] = entry['kind']
        row.update({field: data[field] for field in data if field != 'extras'})
        if 'extras' in data:
            if not isinstance(data['extras'], dict):
                raise DexError('extras is an object of named values.', 'invalid_input')
            row['extras'] = {**entry['extras'], **data['extras']}  # keys sent replace; the rest stay
        _check(con, row, entry_id)
        if row['blind_box'] is not None:
            row['blind_box'] = bool(row['blind_box'])
        if all(row[field] == entry[field] for field in EDITABLE):
            return  # nothing changed: no new version
        moved = row['parent_id'] != entry['parent_id']
        con.execute(
            """UPDATE dex_entries SET name=?, parent_id=?, partner_id=?, character_id=?, ip_id=?, blind_box=?, rarity=?,
                      product_id=?, extras=?, sort_order=?, version=version+1, updated_at=datetime('now'), updated_by=?
               WHERE id=?""",
            (row['name'], row['parent_id'], row['partner_id'], row['character_id'], row['ip_id'],
             None if row['blind_box'] is None else int(row['blind_box']), row['rarity'], row['product_id'],
             json.dumps(row['extras'], ensure_ascii=False),
             _next_order(con, row['parent_id'], entry['kind']) if moved else entry['sort_order'],
             _actor(actor), entry_id))
    _write(con, work)
    return detail(con, entry_id)


def reorder(con, data, actor):
    """Renumber one group of siblings (one kind under one parent, or the top companies or partners) in the
    order given. The ids must be exactly that group."""
    data = _body(data)
    parent_id = _id_or_none(data.get('parent_id'), 'parent_id')
    ids = data.get('ids')
    if not isinstance(ids, list) or not ids or any(isinstance(i, bool) or not isinstance(i, int) for i in ids) \
            or len(set(ids)) != len(ids):
        raise DexError('ids is the list of the entries in their new order.', 'invalid_input')

    def work():
        if parent_id is not None:
            _need(con, parent_id)
        first = _brief(con, ids[0])
        if first is None:
            raise DexError('That Dex entry does not exist.', 'dex_not_found', 404)
        group = {row['id']: row['sort_order'] for row in con.execute(
            'SELECT id, sort_order FROM dex_entries WHERE parent_id IS ? AND kind=? AND deleted_at IS NULL',
            (parent_id, first['kind']))}
        if set(group) != set(ids):
            raise DexError('The list does not match the entries here; reload and try again.', 'order_stale', 409)
        for position, entry_id in enumerate(ids, start=1):
            if group[entry_id] != position * 10:
                con.execute("""UPDATE dex_entries SET sort_order=?, version=version+1, updated_at=datetime('now'),
                               updated_by=? WHERE id=?""", (position * 10, _actor(actor), entry_id))
    _write(con, work)
    return children(con, parent_id) if parent_id is not None else top(con, _brief(con, ids[0])['kind'])


def set_website_number(con, entry_id, website_id, website_item, actor):
    """Give an entry the website's own number, for a row the website holds that the load did not load (owner's
    review, 2026-11-14), so a later step joins the website's row instead of making it twice. Refused when another
    entry of the kind (live, or kept for the website) holds the number, or the entry already has another number.
    Returns True when it changed something."""
    if isinstance(website_id, bool) or not isinstance(website_id, int) or website_id <= 0:
        raise DexError('website_id is a positive number.', 'invalid_input')
    website_item = 1 if website_item else 0

    def work():
        entry = _need(con, entry_id)
        if website_item and entry['kind'] != 'product':
            raise DexError('Only a product is an item figure on the website.', 'field_not_allowed')
        now = con.execute('SELECT website_id, website_item FROM dex_entries WHERE id=?', (entry_id,)).fetchone()
        if now['website_id'] == website_id and now['website_item'] == website_item:
            return False
        if now['website_id'] is not None:
            raise DexError(f'This entry already has website number {now["website_id"]}.', 'website_number_set', 409)
        other = con.execute("""SELECT id, name, deleted_at FROM dex_entries WHERE kind=? AND website_item=? AND website_id=?
                               AND id<>?""", (entry['kind'], website_item, website_id, entry_id)).fetchone()
        if other:
            raise DexError(f'Website number {website_id} is already held by “{other["name"]}”'
                           + (' (deleted, kept for the website).' if other['deleted_at'] else '.'), 'website_number_taken', 409)
        con.execute("""UPDATE dex_entries SET website_id=?, website_item=?, version=version+1, updated_at=datetime('now'),
                       updated_by=? WHERE id=?""", (website_id, website_item, _actor(actor), entry_id))
        return True
    return _write(con, work)


def _subtree(con, entry_id):
    return [row[0] for row in con.execute(
        """WITH RECURSIVE under(id, depth) AS (
               SELECT id, 0 FROM dex_entries WHERE id=? AND deleted_at IS NULL
               UNION ALL
               SELECT c.id, under.depth + 1 FROM dex_entries c JOIN under ON c.parent_id=under.id
               WHERE c.deleted_at IS NULL)
           SELECT id FROM under ORDER BY depth DESC, id""", (entry_id,))]


def removal(con, entry_id):
    """What a delete would take: counts by kind, and why it is refused when it is."""
    _need(con, entry_id)
    ids = _subtree(con, entry_id)
    marks = ','.join('?' * len(ids))
    counts = {row[0]: row[1] for row in con.execute(
        f'SELECT kind, COUNT(*) FROM dex_entries WHERE id IN ({marks}) GROUP BY kind', ids)}
    linked = [dict(row) for row in con.execute(
        f"""SELECT e.id, e.name, p.sku FROM dex_entries e JOIN products p ON p.id=e.product_id
            WHERE e.id IN ({marks}) ORDER BY e.id""", ids)]
    used = [dict(row) for row in con.execute(
        f"""SELECT id, kind, name FROM dex_entries WHERE deleted_at IS NULL AND id NOT IN ({marks})
            AND (partner_id IN ({marks}) OR character_id IN ({marks}) OR ip_id IN ({marks})) ORDER BY id LIMIT 20""",
        ids * 4)]
    return {'ids': ids, 'counts': counts, 'linked': linked, 'used_by': used}


def delete(con, entry_id, actor):
    def work():
        plan = removal(con, entry_id)
        if plan['linked']:
            first = plan['linked'][0]
            raise DexError(f'“{first["name"]}” is linked to {first["sku"]}. Unlink it first.', 'dex_linked', 409)
        if plan['used_by']:
            first = plan['used_by'][0]
            raise DexError(f'“{first["name"]}” names it as its partner, character or IP. Change that first.', 'dex_in_use', 409)
        kept = removed = 0
        for one in plan['ids']:  # children first
            website_id = con.execute('SELECT website_id FROM dex_entries WHERE id=?', (one,)).fetchone()[0]
            if website_id is None:
                con.execute('DELETE FROM dex_entries WHERE id=?', (one,))
                removed += 1
            else:  # the website has it: keep the row until the website is told (a later step)
                con.execute("""UPDATE dex_entries SET deleted_at=datetime('now'), version=version+1,
                               updated_at=datetime('now'), updated_by=? WHERE id=?""", (_actor(actor), one))
                kept += 1
        return {'removed': removed, 'kept_for_website': kept, 'counts': plan['counts']}
    return _write(con, work)
