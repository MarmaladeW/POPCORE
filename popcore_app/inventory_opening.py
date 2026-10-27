"""Count sheets and reviewed opening-count intake for real inventory activation.

The public commands route never posts an opening. Opening counts enter only
through ``scripts/inventory_opening.py``, which reviews completed count files
here and applies them as one opening document inside a held write lock. Each
store goes live with its own opening, and a counted product that is not yet
verified is verified by the stock form written on its count row.
"""
import csv
import hashlib
import io
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from auth import ROLE_CLAIM
from catalog_identity import FORMS_TO_UNITS, update_product_identity
from db import esc_csv
from identity_sheet import SUGGESTED_IDENTITY
from inventory_commands import _post_inventory_in_transaction, live_store_ids


COUNT_SHEET_COLUMNS = (
    'store', 'location', 'product_id', 'sku', '记账名', 'name', 'design', 'barcode',
    'stock_form', 'unit', 'identity', 'count', 'notes',
)
COUNT_SHEET_SCOPES = ('legacy', 'catalog')
REQUIRED_COUNT_COLUMNS = ('store', 'location', 'unit', 'count')
DEFAULT_OPENING_REASON = 'Reviewed physical opening count'
# Forms a count row may verify on its own. Sealed sets need a reviewed conversion and
# confirmed designs a series and design name, so they are set up in Products first.
COUNT_VERIFIABLE_FORMS = ('random_box', 'ordinary')


class OpeningCountError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def legacy_column(location_code):
    """Legacy stock column that the compatibility projection maps a location to (display has none)."""
    if location_code == 'display':
        return None
    return {'floor': 'instore_qty', 'claw': 'claw_qty'}.get(location_code, 'upstairs_qty')


def trusts_legacy(location_code):
    """Legacy claw_qty overlapped the floor count, and display never had a legacy column,
    so neither is compared or kept."""
    return location_code not in ('claw', 'display')


def active_locations(con):
    return [dict(row) for row in con.execute(
        """SELECT l.id, l.store_id, s.code AS store_code, l.code, l.name,
                  COALESCE(ss.opening_verified, 0) AS opening_verified
           FROM inventory_locations l
           JOIN stores s ON s.id=l.store_id
           LEFT JOIN inventory_scope_state ss ON ss.location_id=l.id
           WHERE l.is_active=1 AND s.is_active=1
           ORDER BY s.code, l.id""")]


def find_location(con, store_code, location_code):
    store_code = (store_code or '').strip().upper()
    location_code = (location_code or '').strip().lower()
    for location in active_locations(con):
        if location['store_code'] == store_code and location['code'] == location_code:
            return location
    return None


def count_sheet_rows(con, store_code, location_code, scope='legacy'):
    """Blind count rows for one location: no expected quantities are included."""
    if scope not in COUNT_SHEET_SCOPES:
        raise OpeningCountError('scope must be legacy or catalog', 'invalid_scope')
    location = find_location(con, store_code, location_code)
    if location is None:
        raise OpeningCountError(
            'store and location must name an active inventory location',
            'location_unknown',
        )
    where = ''
    params = []
    if scope == 'legacy':
        # A sealed set (整端) has no legacy stock of its own; it is listed with its blind box.
        where = """WHERE EXISTS (
            SELECT 1 FROM stock st WHERE st.store_id=?
              AND (st.instore_qty != 0 OR st.upstairs_qty != 0 OR st.claw_qty != 0)
              AND (st.product_id=p.id OR st.product_id IN (
                   SELECT target_product_id FROM product_conversions WHERE source_product_id=p.id)))"""
        params.append(location['store_id'])
    rows = con.execute(
        f"""SELECT p.id, p.sku, p.jizhanming, COALESCE(NULLIF(p.name_cn_en, ''), p.jizhanming) AS name,
                   p.design_name, p.stock_form, p.stock_unit, p.identity_status, p.product_type,
                   (SELECT GROUP_CONCAT(code, ' | ') FROM (
                        SELECT DISTINCT code FROM product_barcodes
                        WHERE product_id=p.id ORDER BY code)) AS barcodes
            FROM products p
            LEFT JOIN product_series ps ON ps.id=p.series_id
            {where}
            ORDER BY COALESCE(ps.name, p.ip_series, ''), p.design_name, p.sku, p.id""",
        params,
    ).fetchall()

    def identity(row):
        # An unverified product is prefilled from its product type; counting it verifies that form.
        if row['identity_status'] != 'verified' and not row['stock_form'] and not row['stock_unit']:
            return SUGGESTED_IDENTITY.get((row['product_type'] or '').strip(), ('', ''))
        return row['stock_form'] or '', row['stock_unit'] or ''

    return [{
        'store': location['store_code'],
        'location': location['code'],
        'product_id': row['id'],
        'sku': row['sku'] or '',
        '记账名': row['jizhanming'] or '',
        'name': row['name'] or '',
        'design': row['design_name'] or '',
        'barcode': row['barcodes'] or '',
        'stock_form': identity(row)[0],
        'unit': identity(row)[1],
        'identity': row['identity_status'],
        'count': '',
        'notes': '',
    } for row in rows]


def count_sheet_csv(rows):
    lines = ['﻿' + ','.join(COUNT_SHEET_COLUMNS)]
    for row in rows:
        lines.append(','.join(esc_csv(row[column]) for column in COUNT_SHEET_COLUMNS))
    return '\n'.join(lines) + '\n'


def read_count_file(path):
    """Read one completed count CSV into rows tagged with file and line number."""
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        headers = [(name or '').strip().lower() for name in (reader.fieldnames or [])]
        missing = [name for name in REQUIRED_COUNT_COLUMNS if name not in headers]
        if 'product_id' not in headers and 'sku' not in headers:
            missing.append('product_id or sku')
        if missing:
            raise OpeningCountError(
                f'{path}: missing column(s): {", ".join(missing)}', 'missing_column'
            )
        reader.fieldnames = headers
        rows = []
        for raw in reader:
            values = {key: (value or '').strip() for key, value in raw.items()
                      if key is not None and isinstance(value, str)}
            if not any(values.values()):
                continue
            values['_file'] = str(path)
            values['_row'] = reader.line_num
            rows.append(values)
        return rows


def _unsheeted(value):
    """Undo the CSV formula guard that esc_csv adds to exported text."""
    return value[1:] if value.startswith("'") and value[1:2] in ('=', '+', '-', '@') else value


def _parse_count(value):
    if value == '':
        return None
    if not (value.isascii() and value.isdigit()):
        raise ValueError('count must be a whole number of native units (0 or more)')
    return int(value)


def _key(location):
    return f"{location['store_code']}-{location['code']}"


def review_opening_counts(con, rows, *, empty_locations=(), actor_sub=None,
                          business_date=None, reason=DEFAULT_OPENING_REASON, stores=None):
    """Validate completed count rows and compare them with legacy stock.

    Reads only. ``stores`` names the stores going live (by default every store
    that is not live yet); every active location of those stores must be
    counted. Returns a JSON-safe report whose ``counts`` are the accepted rows
    that apply would post (positive counts) or record as zero, and whose
    ``verified_by_count`` are the unverified products the counts verify.
    """
    every_location = active_locations(con)
    live = live_store_ids(con)
    live_codes = {loc['store_code'] for loc in every_location if loc['store_id'] in live}
    by_code = {(loc['store_code'], loc['code']): loc for loc in every_location}
    errors = []
    warnings = []
    submitted = set()
    counted = set()
    accepted = {}
    verify = {}
    closed = set()

    def error(row, code, message, **extra):
        errors.append({'file': row.get('_file') if row else None,
                       'row': row.get('_row') if row else None,
                       'code': code, 'message': message, **extra})

    if stores:
        opening_stores = {code.strip().upper() for code in stores}
        for code in sorted(opening_stores - {loc['store_code'] for loc in every_location}):
            error(None, 'store_unknown', f'{code} has no active inventory location')
    else:
        opening_stores = {loc['store_code'] for loc in every_location} - live_codes
    locations = [loc for loc in every_location if loc['store_code'] in opening_stores]

    def outside(row, location, label):
        """A live store is closed; another store must be named to be counted now."""
        if location['store_code'] in live_codes:
            closed.add(location['store_code'])
        elif location['store_code'] not in opening_stores:
            error(row, 'location_outside_opening',
                  f'{label} is not in this opening ({", ".join(sorted(opening_stores)) or "no store"}); '
                  f'pass --store {location["store_code"]} to open it too')
            return True
        return False

    for value in empty_locations:
        store_code, _, location_code = value.partition('-')
        location = by_code.get((store_code.strip().upper(), location_code.strip().lower()))
        if location is None:
            error(None, 'location_unknown', f'--empty-location {value} is not an active location')
        elif not outside(None, location, f'--empty-location {value}'):
            submitted.add(location['id'])
            counted.add(location['id'])

    for row in rows:
        location = by_code.get((row.get('store', '').upper(), row.get('location', '').lower()))
        if location is None:
            error(row, 'location_unknown',
                  'store and location must name an active inventory location')
            continue
        if outside(row, location, _key(location)):
            continue
        submitted.add(location['id'])
        try:
            count = _parse_count(row.get('count', ''))
        except ValueError as exc:
            error(row, 'count_invalid', str(exc))
            continue
        if count is None:
            continue
        counted.add(location['id'])

        raw_id = row.get('product_id', '')
        sku = _unsheeted(row.get('sku', ''))
        if not raw_id and not sku:
            error(row, 'identity_missing',
                  'a counted row needs an exact product_id or SKU; names and barcodes are not accepted')
            continue
        product = None
        if raw_id:
            if not (raw_id.isascii() and raw_id.isdigit()):
                error(row, 'product_unknown', 'product_id must be a whole number')
                continue
            product = con.execute('SELECT * FROM products WHERE id=?', (int(raw_id),)).fetchone()
            if product is None:
                error(row, 'product_unknown', f'product_id {raw_id} does not exist')
                continue
            if sku and product['sku'] != sku:
                error(row, 'identity_mismatch',
                      f'product_id {raw_id} has SKU {product["sku"]!r}, not {sku!r}')
                continue
        else:
            matches = con.execute('SELECT * FROM products WHERE sku=?', (sku,)).fetchall()
            if len(matches) != 1:
                error(row, 'sku_unknown' if not matches else 'identity_ambiguous',
                      f'SKU {sku!r} matches {len(matches)} products exactly')
                continue
            product = matches[0]
        form = row.get('stock_form', '').lower()
        unit = row.get('unit', '').lower()
        if product['identity_status'] == 'verified':
            native = product['stock_unit']
            if form and form != product['stock_form']:
                error(row, 'identity_changed',
                      f'product {product["id"]} is verified as {product["stock_form"]}; '
                      'change its identity in Products, not on the count sheet',
                      product_id=product['id'])
                continue
        else:
            # Counting an unverified product verifies the form written on its row.
            if not form:
                error(row, 'identity_unverified',
                      f'product {product["id"]} is not verified; write its stock_form '
                      f'({" or ".join(COUNT_VERIFIABLE_FORMS)}) or set it up in Products',
                      product_id=product['id'])
                continue
            if form not in COUNT_VERIFIABLE_FORMS:
                error(row, 'identity_form_unsupported',
                      f'a count verifies {" or ".join(COUNT_VERIFIABLE_FORMS)} only; '
                      f'set up {form} products in Products first', product_id=product['id'])
                continue
            if product['stock_form'] and form != product['stock_form']:
                error(row, 'identity_changed',
                      f'product {product["id"]} is set up as {product["stock_form"]}, not {form}; '
                      'change it in Products first', product_id=product['id'])
                continue
            native = FORMS_TO_UNITS[form]
            earlier = verify.get(product['id'])
            if earlier and earlier['stock_form'] != form:
                error(row, 'identity_conflict',
                      f'product {product["id"]} is counted as {earlier["stock_form"]} '
                      f'in {earlier["file"]} row {earlier["row"]} and as {form} here',
                      product_id=product['id'])
                continue
        # A blank unit is the native one, also once an earlier apply of these files verified the product.
        unit = unit or native
        if unit != native:
            error(row, 'unit_mismatch',
                  f'unit {unit or "(blank)"} does not match native unit {native}',
                  product_id=product['id'])
            continue
        if product['identity_status'] != 'verified':
            verify.setdefault(product['id'], {
                'product_id': product['id'], 'sku': product['sku'], 'stock_form': form,
                'stock_unit': native, 'file': row['_file'], 'row': row['_row'],
            })
        slot = (product['id'], location['id'])
        if slot in accepted:
            first = accepted[slot]
            error(row, 'duplicate_count',
                  f'product {product["id"]} at {_key(location)} is also counted at '
                  f'{first["file"]} row {first["row"]}', product_id=product['id'])
            first['duplicate'] = True
            continue
        legacy = con.execute(
            f"SELECT {legacy_column(location['code'])} AS qty FROM stock "
            'WHERE product_id=? AND store_id=?', (product['id'], location['store_id']),
        ).fetchone() if trusts_legacy(location['code']) else None
        legacy_qty = legacy['qty'] if legacy else None
        accepted[slot] = {
            'file': row['_file'], 'row': row['_row'],
            'product_id': product['id'], 'sku': product['sku'],
            'store': location['store_code'], 'location': location['code'],
            'location_id': location['id'], 'unit': unit, 'count': count,
            'stock_form': form, 'legacy_qty': legacy_qty,
            'difference': count - (legacy_qty or 0),
        }
    for slot, line in list(accepted.items()):
        if line.pop('duplicate', False):
            error({'_file': line['file'], '_row': line['row']}, 'duplicate_count',
                  f'product {line["product_id"]} at {line["store"]}-{line["location"]} '
                  'is counted more than once', product_id=line['product_id'])
            del accepted[slot]

    for location in locations:
        if location['id'] not in submitted:
            error(None, 'location_not_counted',
                  f'{_key(location)} has no count rows; count it or pass '
                  f'--empty-location {_key(location)}')
            continue
        if location['id'] not in counted:
            # A sheet of blanks is not a count; never open it as an authoritative zero.
            error(None, 'location_not_counted',
                  f'{_key(location)} has count rows but every count is blank; enter the '
                  f'counts (0 where none was found) or pass --empty-location {_key(location)}')
            continue
        if not trusts_legacy(location['code']):
            continue
        column = legacy_column(location['code'])
        for legacy in con.execute(
            f"""SELECT p.id, p.sku, p.identity_status, st.{column} AS qty
                FROM stock st JOIN products p ON p.id=st.product_id
                WHERE st.store_id=? AND st.{column} != 0 ORDER BY p.id""",
            (location['store_id'],),
        ):
            if (legacy['id'], location['id']) in accepted:
                continue
            if legacy['identity_status'] == 'verified' or legacy['id'] in verify:
                error(None, 'uncounted_legacy_stock',
                      f'product {legacy["id"]} ({legacy["sku"]}) has legacy {column}='
                      f'{legacy["qty"]} at {_key(location)} but no count; write 0 if none was found',
                      product_id=legacy['id'])
            else:
                warnings.append({
                    'code': 'unverified_legacy_stock', 'product_id': legacy['id'],
                    'sku': legacy['sku'], 'location': _key(location),
                    'legacy_qty': legacy['qty'],
                    'message': 'legacy quantity stays untrusted until the identity is verified and counted',
                })
    for legacy in con.execute(
        """SELECT st.product_id, p.sku, s.code AS store, st.claw_qty,
                  EXISTS (SELECT 1 FROM inventory_locations
                          WHERE store_id=st.store_id AND code='claw' AND is_active=1) AS has_claw
           FROM stock st JOIN products p ON p.id=st.product_id
           JOIN stores s ON s.id=st.store_id
           WHERE st.claw_qty != 0 AND st.store_id IN
             (SELECT store_id FROM inventory_locations WHERE is_active=1)
           ORDER BY st.product_id"""
    ):
        if legacy['store'] not in opening_stores:
            continue
        warnings.append({
            'code': 'legacy_claw_unverified', 'product_id': legacy['product_id'],
            'sku': legacy['sku'], 'store': legacy['store'],
            'legacy_qty': legacy['claw_qty'],
            'message': ('claw_qty is not a physical balance; the claw count replaces it'
                        if legacy['has_claw'] else
                        'claw_qty is not a physical balance and is not opened'),
        })

    # A restock sheet left open would post its old pick against the counted balances once
    # the store is live, so staff finish it in legacy mode or delete it before the count.
    for store_code in sorted(opening_stores - live_codes):
        sheets = [dict(row) for row in con.execute(
            """SELECT rs.id, rs.date, rs.status FROM restock_sessions rs
               JOIN stores s ON s.id=rs.store_id
               WHERE s.code=? AND (rs.status IN ('submitted','picking') OR (
                     rs.status='pending' AND EXISTS (
                         SELECT 1 FROM restock_items ri WHERE ri.session_id=rs.id)))
               ORDER BY rs.date, rs.id""", (store_code,))]
        if sheets:
            error(None, 'open_restock_sessions',
                  f'{store_code} has open restock sheets '
                  + ', '.join(f'#{s["id"]} ({s["date"]}, {s["status"]})' for s in sheets)
                  + '; complete or delete them before the count', sessions=sheets)

    mode = con.execute('SELECT mode FROM inventory_mode WHERE id=1').fetchone()['mode']
    for store_code in sorted((opening_stores | closed) & live_codes):
        error(None, 'opening_closed', f'{store_code} is already live; its opening is closed')

    if actor_sub:
        granted = {row['store_id'] for row in con.execute(
            'SELECT store_id FROM inventory_access WHERE auth0_sub=?', (actor_sub,))}
        for store_id, store_code in sorted({(l['store_id'], l['store_code']) for l in locations}):
            if store_id not in granted:
                error(None, 'actor_access_missing',
                      f'{actor_sub} has no inventory_access grant for {store_code}')

    lines = sorted(accepted.values(), key=lambda line: (line['location_id'], line['product_id']))
    if not errors and not any(line['count'] for line in lines):
        error(None, 'no_positive_counts', 'an opening needs at least one counted unit')
    location_summary = []
    for location in locations:
        mine = [line for line in lines if line['location_id'] == location['id']]
        location_summary.append({
            'location': _key(location), 'location_id': location['id'],
            'submitted': location['id'] in submitted,
            'counted_rows': len(mine),
            'counted_units': sum(line['count'] for line in mine),
            'legacy_units_for_counted_rows': sum(line['legacy_qty'] or 0 for line in mine),
            'matching_legacy': sum(1 for line in mine if line['difference'] == 0),
            'above_legacy': sum(1 for line in mine if line['difference'] > 0),
            'below_legacy': sum(1 for line in mine if line['difference'] < 0),
        })
    # Over every location, so replaying an applied opening yields the same request key.
    empty = sorted(_key(loc) for loc in every_location
                   if loc['id'] in submitted and not any(
                       line['count'] and line['location_id'] == loc['id'] for line in lines))
    verified_by_count = [
        {key: item[key] for key in ('product_id', 'sku', 'stock_form', 'stock_unit')}
        for _, item in sorted(verify.items())
    ]
    # Only the submitted rows, each with the form written on it, so rerunning the same files
    # after an apply that verified their products finds the applied opening.
    intent = {
        'business_date': business_date, 'reason': reason,
        'counts': [[line['product_id'], line['location_id'], line['count'], line['stock_form']]
                   for line in lines],
        'empty_locations': empty,
    }
    digest = hashlib.sha256(
        json.dumps(intent, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    return {
        'inventory_mode': mode,
        'stores': sorted(opening_stores),
        'business_date': business_date,
        'request_key': f'opening-count-{digest[:32]}',
        'ready_to_apply': not errors,
        'locations': location_summary,
        'counts': lines,
        'verified_by_count': verified_by_count,
        'errors': errors,
        'warnings': warnings,
        'summary': {
            'rows_read': len(rows),
            'counted_rows': len(lines),
            'counted_units': sum(line['count'] for line in lines),
            'differences_from_legacy': sum(1 for line in lines if line['difference']),
            'verified_by_count': len(verified_by_count),
            'errors': len(errors),
            'warnings': len(warnings),
        },
    }


def reconcile_inventory(con, foreign_key_baseline=None):
    """Compare movements with balances both ways and balances with legacy rows."""
    integrity = con.execute('PRAGMA integrity_check').fetchone()[0]
    foreign_keys = sorted(tuple(row) for row in con.execute('PRAGMA foreign_key_check'))
    movements = {(r[0], r[1], r[2]): r[3] for r in con.execute(
        """SELECT product_id, location_id, disposition, SUM(quantity)
           FROM inventory_movements GROUP BY product_id, location_id, disposition""")}
    balances = {(r[0], r[1], r[2]): r[3] for r in con.execute(
        'SELECT product_id, location_id, disposition, quantity FROM inventory_balances')}
    ledger_differences = [
        {'product_id': key[0], 'location_id': key[1], 'disposition': key[2],
         'movements': movements.get(key), 'balance': balances.get(key)}
        for key in sorted(set(movements) | set(balances))
        if movements.get(key, 0) != balances.get(key, 0)
    ]
    compatibility_differences = []
    # Only live stores keep legacy rows in step with the ledger; the others stay legacy.
    for row in con.execute(
        """SELECT p.id AS product_id, s.store_id, s.instore_qty, s.upstairs_qty, s.claw_qty,
                  (SELECT COALESCE(SUM(b.quantity), 0) FROM inventory_balances b
                   JOIN inventory_locations l ON l.id=b.location_id
                   WHERE b.product_id=p.id AND l.store_id=s.store_id
                     AND b.disposition='saleable' AND l.code='floor') AS floor_qty,
                  (SELECT COALESCE(SUM(b.quantity), 0) FROM inventory_balances b
                   JOIN inventory_locations l ON l.id=b.location_id
                   WHERE b.product_id=p.id AND l.store_id=s.store_id
                     AND b.disposition='saleable'
                     AND l.code IN ('upstairs','warehouse')) AS back_qty,
                  (SELECT CASE WHEN COUNT(l.id)=0 THEN NULL ELSE COALESCE(SUM(b.quantity), 0) END
                   FROM inventory_locations l
                   LEFT JOIN inventory_balances b ON b.location_id=l.id
                     AND b.product_id=p.id AND b.disposition='saleable'
                   WHERE l.store_id=s.store_id AND l.code='claw'
                     AND l.is_active=1) AS claw_balance
           FROM stock s JOIN products p ON p.id=s.product_id
           WHERE p.identity_status='verified' AND s.store_id IN
             (SELECT l.store_id FROM inventory_locations l
              JOIN inventory_scope_state ss ON ss.location_id=l.id
              WHERE l.is_active=1 AND ss.opening_verified=1)"""
    ):
        # Stores without a claw location keep their legacy claw_qty untouched.
        claw_differs = row['claw_balance'] is not None and row['claw_qty'] != row['claw_balance']
        if (row['instore_qty'], row['upstairs_qty']) != (row['floor_qty'], row['back_qty']) \
                or claw_differs:
            compatibility_differences.append(dict(row))
    for row in con.execute(
        """SELECT DISTINCT b.product_id, l.store_id FROM inventory_balances b
           JOIN inventory_locations l ON l.id=b.location_id
           WHERE b.disposition='saleable' AND b.quantity != 0
             AND l.code IN ('floor','upstairs','warehouse','claw')
             AND NOT EXISTS (SELECT 1 FROM stock s
                             WHERE s.product_id=b.product_id AND s.store_id=l.store_id)"""
    ):
        compatibility_differences.append({**dict(row), 'stock_row': None})
    new_foreign_keys = (foreign_keys if foreign_key_baseline is None
                        else sorted(set(foreign_keys) - set(foreign_key_baseline)))
    return {
        'integrity': integrity,
        'foreign_key_errors': len(foreign_keys),
        'new_foreign_key_errors': [list(row) for row in new_foreign_keys],
        'ledger_differences': ledger_differences,
        'compatibility_differences': compatibility_differences,
        'ok': (integrity == 'ok' and not new_foreign_keys
               and not ledger_differences and not compatibility_differences),
    }


def connect(path, *, read_only):
    if read_only:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    else:
        con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


def apply_opening_counts(db_path, rows, *, actor_sub, business_date, backup_path,
                         empty_locations=(), reason=DEFAULT_OPENING_REASON,
                         busy_timeout_ms=5000, stores=None):
    """Back up, verify counted identities, post one opening document, verify
    every location of the opening stores, then commit.

    The write lock is taken before review and held through commit, so no app
    writer can change stock between the reviewed comparison, the backup and
    the posting. Any failed check rolls the whole cutover back.
    """
    if not actor_sub or not business_date:
        raise OpeningCountError('apply needs an actor and a business date', 'invalid_input')
    db_path, backup_path = Path(db_path).resolve(), Path(backup_path).resolve()
    if not db_path.is_file():
        raise OpeningCountError(f'database does not exist: {db_path}', 'invalid_input')
    if backup_path.exists() or backup_path == db_path:
        raise OpeningCountError(f'refusing existing backup path: {backup_path}', 'invalid_input')
    con = connect(db_path, read_only=False)
    try:
        con.execute(f'PRAGMA busy_timeout={int(busy_timeout_ms)}')
        con.execute('BEGIN IMMEDIATE')
        review = review_opening_counts(
            con, rows, empty_locations=empty_locations, actor_sub=actor_sub,
            business_date=business_date, reason=reason, stores=stores,
        )
        prior = con.execute(
            "SELECT id, status FROM inventory_documents WHERE request_key=?",
            (review['request_key'],),
        ).fetchone()
        if prior is not None:
            reconciliation = reconcile_inventory(con)
            con.rollback()
            return {**review, 'errors': [], 'ready_to_apply': False,
                    'applied': False, 'already_applied': True,
                    'opening_document_id': prior['id'],
                    'reconciliation': reconciliation}
        if not review['ready_to_apply']:
            con.rollback()
            return {**review, 'applied': False}

        integrity_before = con.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity_before != 'ok':
            raise OpeningCountError(f'integrity_check before cutover: {integrity_before}',
                                    'integrity_failed')
        foreign_key_baseline = [tuple(row) for row in con.execute('PRAGMA foreign_key_check')]

        # A second reader sees exactly the committed state this lock protects.
        with closing(sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)) as source, \
                closing(sqlite3.connect(backup_path)) as target:
            source.backup(target)
            backup_integrity = target.execute('PRAGMA integrity_check').fetchone()[0]
        if backup_integrity != 'ok':
            raise OpeningCountError('backup failed integrity_check', 'backup_failed')

        for item in review['verified_by_count']:
            try:
                update_product_identity(con, item['product_id'], {
                    'stock_form': item['stock_form'], 'stock_unit': item['stock_unit'],
                    'identity_status': 'verified',
                })
            except ValueError as exc:
                raise OpeningCountError(
                    f'product {item["product_id"]} cannot be verified as {item["stock_form"]}: {exc}',
                    getattr(exc, 'code', 'identity_invalid'),
                ) from exc

        payload_lines = [{
            'product_id': line['product_id'], 'quantity': line['count'],
            'unit': line['unit'], 'to_location_id': line['location_id'],
            'to_disposition': 'saleable', 'expected_versions': {'to': 0},
        } for line in review['counts'] if line['count'] > 0]
        actor = {'sub': actor_sub, ROLE_CLAIM: 'admin'}
        result = _post_inventory_in_transaction(con, {
            'kind': 'opening', 'business_date': business_date,
            'reason': reason, 'lines': payload_lines,
        }, actor=actor, request_key=review['request_key'])
        document_id = result['document_id']

        # Locations counted as empty, or whose counts were all zero, are still
        # reviewed openings; they share the one opening document.
        con.execute(
            f"""UPDATE inventory_scope_state SET opening_verified=1, opening_document_id=?
                WHERE opening_verified=0 AND location_id IN
                  ({','.join('?' for _ in review['locations'])})""",
            (document_id, *(loc['location_id'] for loc in review['locations'])),
        )
        for line in review['counts']:
            if line['count'] == 0 and line['legacy_qty']:
                column = legacy_column(line['location'])
                con.execute(
                    f"""UPDATE stock SET {column}=0, last_updated=?
                        WHERE product_id=? AND store_id=
                          (SELECT store_id FROM inventory_locations WHERE id=?)""",
                    (business_date, line['product_id'], line['location_id']),
                )
                con.execute(
                    """INSERT INTO stock_transactions
                       (product_id, txn_type, qty, location, date, notes, store_id,
                        inventory_document_id)
                       VALUES (?, 'inventory_opening', 0, ?, ?, ?,
                               (SELECT store_id FROM inventory_locations WHERE id=?), ?)""",
                    (line['product_id'], line['location'], business_date,
                     f'Opening count 0 (legacy {line["legacy_qty"]}); '
                     f'inventory document {document_id}',
                     line['location_id'], document_id),
                )

        # Legacy claw_qty was never a physical balance; the claw count replaces it.
        opening_store_ids = sorted({loc['store_id'] for loc in active_locations(con)
                                    if loc['store_code'] in review['stores']})
        store_marks = ','.join('?' for _ in opening_store_ids) or 'NULL'
        for legacy in con.execute(
            f"""SELECT st.product_id, st.store_id, st.claw_qty FROM stock st
               JOIN inventory_locations l ON l.store_id=st.store_id
                 AND l.code='claw' AND l.is_active=1
               LEFT JOIN inventory_balances b ON b.product_id=st.product_id
                 AND b.location_id=l.id AND b.disposition='saleable'
               WHERE st.claw_qty != COALESCE(b.quantity, 0) AND st.store_id IN ({store_marks})
               ORDER BY st.store_id, st.product_id""", opening_store_ids).fetchall():
            con.execute('UPDATE stock SET claw_qty=0, last_updated=? WHERE product_id=? AND store_id=?',
                        (business_date, legacy['product_id'], legacy['store_id']))
            con.execute(
                """INSERT INTO stock_transactions
                   (product_id, txn_type, qty, location, date, notes, store_id,
                    inventory_document_id)
                   VALUES (?, 'inventory_opening', 0, 'claw', ?, ?, ?, ?)""",
                (legacy['product_id'], business_date,
                 f'Opening count 0 replaces legacy claw_qty {legacy["claw_qty"]}; '
                 f'inventory document {document_id}', legacy['store_id'], document_id),
            )

        unverified = [row['id'] for row in con.execute(
            f"""SELECT l.id FROM inventory_locations l JOIN stores s ON s.id=l.store_id
               LEFT JOIN inventory_scope_state ss ON ss.location_id=l.id
               WHERE l.is_active=1 AND s.is_active=1 AND l.store_id IN ({store_marks})
                 AND COALESCE(ss.opening_verified, 0)=0""", opening_store_ids)]
        mode = con.execute('SELECT mode FROM inventory_mode WHERE id=1').fetchone()['mode']
        if unverified or mode != 'authoritative':
            raise OpeningCountError(
                f'cutover incomplete: unverified locations {unverified}, mode {mode}',
                'cutover_incomplete',
            )
        reconciliation = reconcile_inventory(con, foreign_key_baseline)
        if not reconciliation['ok']:
            raise OpeningCountError(
                'post-opening reconciliation failed: '
                + json.dumps(reconciliation, default=str)[:2000],
                'reconciliation_failed',
            )
        con.commit()
        return {**review, 'inventory_mode': mode, 'applied': True,
                'already_applied': False, 'opening_document_id': document_id,
                'opening_lines': len(payload_lines),
                'backup': {'path': str(backup_path), 'integrity': backup_integrity},
                'reconciliation': reconciliation}
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
    finally:
        con.close()
