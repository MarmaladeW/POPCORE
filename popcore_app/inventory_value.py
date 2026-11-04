"""库存价值 by IP line (owner, 2026-11-03): what each place's stock is worth, split into IP lines such as
Smiski, Sonny Angel, Hirono and Labubu, and what makes up one line.

Managers and admins only, read-only, from the same live ledger as the 库存 overview: saleable stock at
reviewed locations, valued at each product's retail price. A product's IP line is its catalogue
`ip_series`; a confirmed design or sealed set without one takes its series' (or its blind box's). The
catalogue's combined "Smiski & SA" is split into Smiski and Sonny Angel by name. A product with no line
at all is placed by its name (SA → Sonny Angel, SP → SkullPanda, 小野 → Hirono …) and marked as guessed,
so the owner can check it and set the real line in Products; anything still unplaced is 未分类 Unsorted.
"""
import re
from collections import Counter, defaultdict

from inventory_overview import ROLES, _role, sealed_set_of_box
from inventory_catalog import _locations
from inventory_visibility import sees_every_store
from operational_reports import _inventory_identity_valid, _product_name
from stock_losses import cents

UNSORTED = '未分类'
COMBINED = 'Smiski & SA'
SA_WORD = re.compile(r'(^|[^a-z])sa($|[^a-z])')
# Name words for products the catalogue left without a line, most specific first; each maps to an existing
# line name. Short shop abbreviations (SA, SP) must not touch other Latin letters; Chinese may follow them.
NAME_WORDS = [
    ('smiski', 'Smiski'), ('sonny angel', 'Sonny Angel'), ('天使', 'Sonny Angel'), (r'(?<![a-z])sa(?![a-z])', 'Sonny Angel'),
    ('skullpanda', 'SkullPanda'), (r'(?<![a-z])sp(?![a-z])', 'SkullPanda'), ('小野', 'Hirono'), ('hirono', 'Hirono'),
    ('labubu', 'Labubu'), ('the monsters', 'Labubu'), ('zimomo', 'Labubu'), ('molly', 'Molly'), ('dimoo', 'Dimoo'),
    ('crybaby', 'Crybaby'), ('哭娃', 'Crybaby'), ('twinkle twinkle', 'Twinkle Twinkle'), ('星星人', 'Twinkle Twinkle'),
    ('hacipupu', 'Hacipupu'), ('pucky', 'Pucky'), ('kubo', 'Kubo'), ('zsiga', 'Zsiga'), ('nommi', 'Nommi'),
    ('糯米', 'Nommi'), ('peach riot', 'Peach Riot'), ('disney', '迪士尼'), ('迪士尼', '迪士尼'),
    ('harry potter', '哈利波特'), ('哈利波特', '哈利波特'), ('nyota', 'Nyota'), ('marvel', '漫威 Marvel'),
]
NAME_PATTERNS = [(re.compile(word if word.startswith('(?') else re.escape(word)), line) for word, line in NAME_WORDS]


def _split_combined(*products):
    """Smiski or Sonny Angel for a product the catalogue files under "Smiski & SA", by its (or its head's) name."""
    for product in products:
        if product is None:
            continue
        text = ' '.join(str(product[field] or '') for field in ('jizhanming', 'name_cn_en', 'brand')).lower()
        if 'smiski' in text:
            return 'Smiski'
        if 'sonny angel' in text or '天使' in text or SA_WORD.search(text):
            return 'Sonny Angel'
    return COMBINED


def _by_name(*products):
    for product in products:
        if product is None:
            continue
        text = ' '.join(str(product[field] or '') for field in ('jizhanming', 'name_cn_en', 'series_name')).lower()
        for pattern, line in NAME_PATTERNS:
            if pattern.search(text):
                return line
    return None


def ip_lines(products, head_of, box_of_set):
    """Each product's (IP line, guessed from its name); see the module docstring."""
    lines = {}
    for product_id, product in products.items():
        own = (product['ip_series'] or '').strip()
        head = products.get(head_of.get(product['series_id'])) if product['series_id'] is not None else None
        box = products.get(box_of_set.get(product_id))
        box_head = products.get(head_of.get(box['series_id'])) if box and box['series_id'] is not None else None
        inherited = next(((candidate['ip_series'] or '').strip() for candidate in (head, box, box_head)
                          if candidate and (candidate['ip_series'] or '').strip()), '')
        line = own or inherited
        if line == COMBINED:
            lines[product_id] = (_split_combined(product, head, box), False)
        elif line:
            lines[product_id] = (line, False)
        else:
            guess = _by_name(product, head, box)
            lines[product_id] = (guess, True) if guess else (UNSORTED, False)
    return lines


def _money(total):
    return {'units': total['units'], 'value_cents': total['value_cents'], 'unpriced_units': total['unpriced_units'],
            'guessed_units': total['guessed_units']}


def _total():
    return {'units': 0, 'value_cents': 0, 'unpriced_units': 0, 'guessed_units': 0, 'by_unit': Counter()}


def _add(total, quantity, price, unit, guessed=False):
    total['units'] += quantity
    total['guessed_units'] += quantity if guessed else 0
    total['by_unit'][unit] += quantity
    if price is None:
        total['unpriced_units'] += quantity
    else:
        total['value_cents'] += price * quantity


def inventory_value(con, actor, store_code, place=None, line=None):
    if not sees_every_store(actor):
        raise PermissionError('库存价值 Stock value is for managers and admins')
    con.execute('BEGIN')
    try:
        locations = [location for location in _locations(con, actor, store_code) if location['opening_verified']]
        if not locations:
            return {'live': False}
        by_id = {location['id']: location for location in locations}
        roles = sorted({_role(location['code']) for location in locations},
                       key=lambda role: (ROLES.index(role) if role in ROLES else len(ROLES), role))
        place = (place or 'all').strip().lower()
        if place != 'all' and place not in roles:
            raise ValueError('place must be all or one of ' + ', '.join(roles))
        products = {row['id']: dict(row) for row in con.execute(
            '''SELECT p.id,p.sku,p.jizhanming,p.name_cn_en,p.brand,p.ip_series,p.price,p.stock_form,p.stock_unit,
                      p.identity_status,p.series_id,p.design_name,ps.name AS series_name
               FROM products p LEFT JOIN product_series ps ON ps.id=p.series_id''')}
        head_of = {row[0]: row[1] for row in con.execute('SELECT id, product_id FROM product_series WHERE product_id IS NOT NULL')}
        series_of_head = {product_id: series_id for series_id, product_id in head_of.items()}
        box_of_set = {set_id: box_id for box_id, set_id in sealed_set_of_box(con).items()}
        lines = ip_lines(products, head_of, box_of_set)

        places = {role: _total() for role in roles}
        categories = defaultdict(lambda: {**_total(), 'products': set(), 'by_place': defaultdict(_total)})
        groups = defaultdict(lambda: {**_total(), 'members': defaultdict(_total)})
        marks = ','.join('?' for _ in by_id)
        for row in con.execute(
                f'''SELECT product_id,location_id,quantity FROM inventory_balances
                    WHERE disposition='saleable' AND quantity>0 AND location_id IN ({marks})''', list(by_id)):
            product = products.get(row['product_id'])
            if product is None or not _inventory_identity_valid(product):
                continue
            role = _role(by_id[row['location_id']]['code'])
            price, unit, quantity = cents(product['price']), product['stock_unit'], row['quantity']
            _add(places[role], quantity, price, unit, lines[product['id']][1])
            name, guessed = lines[product['id']]
            category = categories[name]
            # A line's split over the places is shown whatever place is picked.
            _add(category['by_place'][role], quantity, price, unit)
            if place != 'all' and role != place:
                continue
            _add(category, quantity, price, unit, guessed)
            category['products'].add(product['id'])
            if name == line:
                # A series' designs and sealed sets go together; a sealed set goes with its blind box.
                base = box_of_set.get(product['id'], product['id'])
                series_id = product['series_id'] if product['series_id'] is not None else \
                    series_of_head.get(base, products[base]['series_id'])
                key = ('series', series_id) if series_id is not None else ('product', base)
                _add(groups[key], quantity, price, unit, guessed)
                _add(groups[key]['members'][product['id']], quantity, price, unit, guessed)

        total = _total()
        for cell in places.values():
            for field in ('units', 'value_cents', 'unpriced_units', 'guessed_units'):
                total[field] += cell[field]
        shown = places[place] if place != 'all' else total

        def share(value):
            return round(value / shown['value_cents'], 4) if shown['value_cents'] else 0

        result = {
            'live': True, 'stores': sorted({location['store_code'] for location in locations}), 'place': place,
            'places': [{'key': role, 'codes': sorted({location['code'] for location in locations if _role(location['code']) == role}),
                        **_money(places[role]), 'by_unit': dict(places[role]['by_unit'])} for role in roles],
            'total': _money(total), 'shown': _money(shown),
            'categories': sorted(({'name': name, **_money(cell), 'products': len(cell['products']),
                                   'by_unit': dict(cell['by_unit']), 'share': share(cell['value_cents'])}
                                  for name, cell in categories.items() if cell['units']),
                                 key=lambda item: (-item['value_cents'], -item['units'], item['name'] == UNSORTED, item['name'])),
            'category': None,
        }
        if line is not None:
            if line not in categories or not categories[line]['units']:
                result['category'] = {'name': line, 'units': 0, 'value_cents': 0, 'unpriced_units': 0, 'guessed_units': 0,
                                      'products': 0, 'by_unit': {}, 'by_place': [], 'groups': []}
                return result
            cell = categories[line]
            series_names = {row[0]: row[1] for row in con.execute('SELECT id, name FROM product_series')}
            items = []
            for (kind, key), group in groups.items():
                if kind == 'series':
                    name, link = series_names.get(key) or f'Series #{key}', {'series_id': key}
                else:
                    name, link = _product_name(products[key]), {'product_id': key}
                members = sorted(({'product_id': product_id, 'name': _product_name(products[product_id]),
                                   'sku': products[product_id]['sku'], 'stock_form': products[product_id]['stock_form'],
                                   'unit': products[product_id]['stock_unit'], **_money(member),
                                   'price_cents': cents(products[product_id]['price']), 'guessed': lines[product_id][1]}
                                  for product_id, member in group['members'].items()),
                                 key=lambda item: (-item['value_cents'], -item['units'], item['name']))
                items.append({'name': name, 'link': link, **_money(group), 'by_unit': dict(group['by_unit']),
                              'share': round(group['value_cents'] / cell['value_cents'], 4) if cell['value_cents'] else 0,
                              'members': members})
            result['category'] = {
                'name': line, **_money(cell), 'products': len(cell['products']), 'by_unit': dict(cell['by_unit']),
                'by_place': [{'key': role, **_money(cell['by_place'][role])} for role in roles if role in cell['by_place']],
                'groups': sorted(items, key=lambda item: (-item['value_cents'], -item['units'], item['name'])),
            }
        return result
    finally:
        if con.in_transaction:
            con.rollback()
