"""Explicit product identity, barcode, and fixed pack-conversion rules."""
import sqlite3

from validation import read_int


FORMS_TO_UNITS = {
    'random_box': 'box',
    'sealed_set': 'set',
    'confirmed_design': 'piece',
    'ordinary': 'piece',
}
IDENTITY_STATUSES = {'unverified', 'verified'}
BARCODE_KINDS = {'manufacturer', 'internal'}
BARCODE_PURPOSES = {'receive', 'move', 'confirmed'}


class CatalogConflict(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def qualifies_for_fresh_set(quantity, set_size):
    quantity = read_int(quantity, 'quantity', minimum=0)
    set_size = read_int(set_size, 'set_size', minimum=1)
    return quantity * 2 > set_size

def receipt_units(boxes_per_pack, pack_count):
    boxes_per_pack = read_int(boxes_per_pack, 'boxes_per_pack', minimum=1)
    pack_count = read_int(pack_count, 'pack_count', minimum=1)
    return read_int(boxes_per_pack * pack_count, 'total_boxes', minimum=1)


def _product(con, product_id):
    row = con.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
    if row is None:
        raise ValueError('product_id does not identify a product')
    return dict(row)


def _table_exists(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _has_product_reference(con, product_id):
    checks = (
        ('stock', 'product_id', 'upstairs_qty != 0 OR instore_qty != 0 OR claw_qty != 0'),
        ('stock_transactions', 'product_id', None),
        ('stock_movements', 'product_id', None),
        ('daily_sales', 'product_id', None),
        ('restock_items', 'product_id', None),
        ('inventory_checks', 'product_id', None),
        ('inventory_document_lines', 'product_id', None),
        ('trade_units', 'design_product_id', None),
    )
    for table, column, extra in checks:
        if not _table_exists(con, table):
            continue
        where = f'{column} = ?'
        if extra:
            where += f' AND ({extra})'
        if con.execute(f'SELECT 1 FROM {table} WHERE {where} LIMIT 1',
                       (product_id,)).fetchone():
            return True
    return False


def _validate_identity(con, product):
    status = product.get('identity_status') or 'unverified'
    form = product.get('stock_form')
    unit = product.get('stock_unit')
    design_name = (product.get('design_name') or '').strip()

    if status not in IDENTITY_STATUSES:
        raise ValueError('identity_status must be unverified or verified')
    if form is not None and form not in FORMS_TO_UNITS:
        raise ValueError('stock_form is invalid')
    if unit is not None and unit not in set(FORMS_TO_UNITS.values()):
        raise ValueError('stock_unit is invalid')
    if form is not None and unit is not None and FORMS_TO_UNITS[form] != unit:
        raise ValueError(f'{form} products must use unit {FORMS_TO_UNITS[form]}')
    if status == 'verified':
        if form is None or unit is None:
            raise ValueError('verified identity requires stock_form and stock_unit')
        if form == 'confirmed_design' and not design_name:
            raise ValueError('verified confirmed_design requires design_name')
        if form == 'sealed_set':
            exists = con.execute(
                'SELECT 1 FROM product_conversions WHERE source_product_id=? LIMIT 1',
                (product['id'],),
            ).fetchone()
            if not exists:
                raise ValueError('verified sealed_set requires a reviewed conversion')


def update_product_identity(con, product_id, updates):
    allowed = {
        'series_id', 'stock_form', 'stock_unit', 'design_name', 'identity_status'
    }
    values = {key: updates[key] for key in allowed if key in updates}
    if not values:
        raise ValueError('No identity fields supplied')

    product = _product(con, product_id)
    if 'series_id' in values and values['series_id'] is not None:
        values['series_id'] = read_int(values['series_id'], 'series_id', minimum=1)
        if not con.execute('SELECT 1 FROM product_series WHERE id=?',
                           (values['series_id'],)).fetchone():
            raise ValueError('series_id does not identify a series')
    for key in ('stock_form', 'stock_unit', 'identity_status'):
        if key in values and values[key] is not None and not isinstance(values[key], str):
            raise ValueError(f'{key} must be text')
    if 'design_name' in values:
        if values['design_name'] is not None and not isinstance(values['design_name'], str):
            raise ValueError('design_name must be text')
        values['design_name'] = (values['design_name'] or '').strip() or None

    semantic = {'series_id', 'stock_form', 'stock_unit', 'design_name'}
    changes_meaning = any(
        key in values and values[key] != product.get(key) for key in semantic
    )
    if (product.get('identity_status') == 'verified' and changes_meaning
            and _has_product_reference(con, product_id)):
        raise CatalogConflict(
            'Referenced product identity cannot be changed in place',
            'identity_locked',
        )

    candidate = dict(product)
    candidate.update(values)
    _validate_identity(con, candidate)
    set_clause = ', '.join(f'{key}=?' for key in values)
    con.execute(
        f'UPDATE products SET {set_clause} WHERE id=?',
        (*values.values(), product_id),
    )
    return candidate


def add_conversion(con, source_product_id, target_product_id,
                   output_per_input, version=None):
    source_product_id = read_int(source_product_id, 'source_product_id', minimum=1)
    target_product_id = read_int(target_product_id, 'target_product_id', minimum=1)
    output_per_input = read_int(output_per_input, 'output_per_input', minimum=1)
    if source_product_id == target_product_id:
        raise ValueError('conversion products must be different')
    source = _product(con, source_product_id)
    target = _product(con, target_product_id)
    if source.get('stock_form') != 'sealed_set' or source.get('stock_unit') != 'set':
        raise ValueError('conversion source must be a sealed_set measured in sets')
    if target.get('stock_form') != 'random_box' or target.get('stock_unit') != 'box':
        raise ValueError('conversion target must be a random_box measured in boxes')
    if source.get('series_id') is None or source.get('series_id') != target.get('series_id'):
        raise ValueError('conversion products must belong to the same explicit series')
    if version is None:
        version = con.execute(
            'SELECT COALESCE(MAX(version), 0) + 1 FROM product_conversions '
            'WHERE source_product_id=?', (source_product_id,)
        ).fetchone()[0]
    version = read_int(version, 'version', minimum=1)
    try:
        cur = con.execute(
            """INSERT INTO product_conversions
               (source_product_id, target_product_id, output_per_input, version)
               VALUES (?, ?, ?, ?)""",
            (source_product_id, target_product_id, output_per_input, version),
        )
    except sqlite3.IntegrityError as exc:
        raise CatalogConflict('Conversion version already exists',
                              'conversion_conflict') from exc
    return {
        'id': cur.lastrowid,
        'source_product_id': source_product_id,
        'target_product_id': target_product_id,
        'output_per_input': output_per_input,
        'version': version,
    }


def _normalize_barcode(code):
    if not isinstance(code, str):
        raise ValueError('code must be text')
    code = code.rstrip('\r\n')
    if not code:
        raise ValueError('code is required')
    return code


def assign_barcode(con, product_id, code, code_kind,
                   input_unit, quantity_per_scan):
    product_id = read_int(product_id, 'product_id', minimum=1)
    code = _normalize_barcode(code)
    if code_kind not in BARCODE_KINDS:
        raise ValueError('code_kind must be manufacturer or internal')
    product = _product(con, product_id)
    if input_unit != product.get('stock_unit') or input_unit not in set(FORMS_TO_UNITS.values()):
        raise ValueError('input_unit must match the product stock_unit')
    quantity_per_scan = read_int(quantity_per_scan, 'quantity_per_scan', minimum=1)

    mappings = con.execute(
        'SELECT product_id, code_kind FROM product_barcodes WHERE code=?',
        (code,),
    ).fetchall()
    if code_kind == 'internal':
        if any(row['product_id'] != product_id or row['code_kind'] != 'internal'
               for row in mappings):
            raise CatalogConflict('Internal barcode is already assigned',
                                  'barcode_conflict')
    elif any(row['code_kind'] == 'internal' for row in mappings):
        raise CatalogConflict('Barcode is reserved by an internal label',
                              'barcode_conflict')

    existing = con.execute(
        """SELECT id FROM product_barcodes
           WHERE code=? AND product_id=? AND code_kind=? AND input_unit=?""",
        (code, product_id, code_kind, input_unit),
    ).fetchone()
    if existing:
        return {'id': existing['id'], 'code': code, 'product_id': product_id}
    cur = con.execute(
        """INSERT INTO product_barcodes
           (code, product_id, code_kind, input_unit, quantity_per_scan)
           VALUES (?, ?, ?, ?, ?)""",
        (code, product_id, code_kind, input_unit, quantity_per_scan),
    )
    return {'id': cur.lastrowid, 'code': code, 'product_id': product_id}


def resolve_barcode(con, code, purpose):
    code = _normalize_barcode(code)
    if purpose not in BARCODE_PURPOSES:
        raise ValueError('purpose must be receive, move, or confirmed')
    rows = con.execute(
        """SELECT pb.product_id, p.stock_unit, pb.quantity_per_scan,
                  pb.code_kind
           FROM product_barcodes pb
           JOIN products p ON p.id=pb.product_id
           WHERE pb.code=?
           ORDER BY pb.product_id""",
        (code,),
    ).fetchall()
    candidates = [dict(row) for row in rows]
    if not candidates:
        return {'status': 'unknown', 'candidates': []}
    if len(candidates) == 1 and not (
        purpose == 'confirmed' and candidates[0]['code_kind'] == 'manufacturer'
    ):
        return {'status': 'exact', 'candidates': candidates}
    return {'status': 'ambiguous', 'candidates': candidates}
