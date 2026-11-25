"""分区盘点 Section count (owner, 2026-11-20): a live store is counted one section every two weeks,
round and round, so every product is counted about once between the three-monthly big counts.

An employee counts and a manager enters the numbers, so this is the manager's screen; the counter
is picked when a section count starts and kept on it.

A *line* is one product with sellable stock at one place. Its *kind* follows the shop: what is on
display, the blind boxes (single boxes and whole sets), the confirmed designs, and everything else.
Its *group* is its IP from the Dex (owner: "dont we have the dex for IPs?"), else its 系列 named as a
Dex IP, else its brand. A section holds parts, each a (kind, group); each kind has one section that
takes its '*' (whatever no part names: a new IP), so every line lands in exactly one section and the
sections together are the whole store. Six sections of about the same number of lines is the
default (owner, 2026-11-20).

Counting itself is 盘点's card (stock_counting.py): a save that names a section count is linked to
it, and the section count's list of lines is taken when it starts, so it stays put while the count
corrects stock. A count that starts also takes every open 待盘点 count-next-time mark of its store
(count_marks.py), whatever section the product is in, and lists those cards first."""
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

import dex_book
from checkout_access import business_date
from dex_load import suggest_ip
from goods_operations import _begin
from inventory_commands import InventoryConflict, InventoryError, InventoryValidationError
from stock_counting import BOXES_ONLY, LABEL_SQL, _image, _names, _read, _scope, _suggested_form
from validation import read_int

KINDS = ('display', 'blind_box', 'design', 'other')
KIND_NAMES = {'display': '展示 Display', 'blind_box': '盲盒 Blind boxes', 'design': '确认款 Confirmed designs',
              'other': '其他 Other products'}
# Which items of a 盘点 card a kind enters (stock_counting._members roles); on display, all of them.
KIND_ROLES = {'blind_box': ('box', 'set'), 'design': ('design',), 'other': ('item',)}
PLACE_ORDER = ('floor', 'upstairs', 'warehouse', 'display', 'claw')
SECTIONS = 6
CYCLE_DAYS = 14          # one section every two weeks (owner, 2026-11-20)
UNEVEN = 1.25            # a section this much over the average asks to be split again
MAX_SECTIONS = 12
STAR = '*'


class SectionError(InventoryError):
    pass


def _placed(code):
    return PLACE_ORDER.index(code) if code in PLACE_ORDER else len(PLACE_ORDER)


def _live_scope(con, actor, store_code):
    scope = _scope(con, actor, store_code)
    if not scope['live']:
        raise InventoryValidationError(f"{scope['store']['code']} is not live yet: 分区盘点 Section counts start "
                                       'once its stock is in POPCORE.', 'store_not_live')
    return scope


def kind_of(location_code, product):
    if location_code == 'display':
        return 'display'
    form = _suggested_form(product)
    if form in ('random_box', 'sealed_set'):
        return 'blind_box'
    if form == 'confirmed_design':
        return 'design'
    return 'other'


def _brand_key(brand):
    folded = re.sub(r'[\s_\-]+', '', (brand or '').strip()).upper()
    return f'brand:{folded}', (f"Other {(brand or '').strip().upper()}" if folded else 'Other')


class _Catalog:
    """Cards (stock_counting._family, read for every product at once) and each card's group."""

    def __init__(self, con):
        self.products = {row['id']: row for row in con.execute(
            f"""SELECT id, sku, {LABEL_SQL} AS label, stock_form, product_type, series_id, ip_series, brand,
                       name_cn_en, price, design_name FROM products""")}
        self.owner = {row['id']: row['product_id'] for row in con.execute('SELECT id, product_id FROM product_series')}
        self.series_name = {row['id']: row['name'] for row in con.execute('SELECT id, name FROM product_series')}
        self.set_box = {row[0]: row[1] for row in con.execute(
            """SELECT c.source_product_id, c.target_product_id FROM product_conversions c
               JOIN products s ON s.id=c.source_product_id WHERE s.stock_form='sealed_set' ORDER BY c.version""")}
        self.members = defaultdict(list)
        for product_id in self.products:
            self.members[self.card_of(product_id)].append(product_id)
        entries = {row['product_id']: row['id'] for row in con.execute(
            'SELECT id, product_id FROM dex_entries WHERE product_id IS NOT NULL AND deleted_at IS NULL')}
        named = dex_book.names(con, list(entries.values())) if entries else {}
        self.dex_ip = {product_id: named[entry]['ip'] for product_id, entry in entries.items()
                       if named.get(entry) and named[entry].get('ip')}
        self.ip_names = {}
        for row in con.execute("SELECT id, name FROM dex_entries WHERE kind IN ('ip', 'partner') AND deleted_at IS NULL "
                               "ORDER BY kind='partner', sort_order, id"):
            self.ip_names.setdefault(row['name'].casefold(), (row['id'], row['name']))
        self._groups = {}

    def card_of(self, product_id):
        """('product', head id) or ('series', id) for a series no product owns, as stock_counting._family."""
        product = self.products[product_id]
        if product['stock_form'] == 'sealed_set' and self.set_box.get(product_id) in self.products:
            product_id = self.set_box[product_id]
            product = self.products[product_id]
        series_id = product['series_id']
        if product['stock_form'] == 'confirmed_design' and series_id is not None:
            owner = self.owner.get(series_id)
            if owner is None or owner not in self.products:
                return ('series', series_id)
            return ('product', owner)
        return ('product', product_id)

    def opens(self, card):
        """The product id that opens this card on 盘点."""
        kind, ident = card
        return ident if kind == 'product' else min(self.members[card])

    def title(self, card):
        kind, ident = card
        return self.products[ident]['label'] if kind == 'product' else self.series_name.get(ident) or ''

    def group(self, card):
        """(group key, name): the card's Dex IP, else its 系列 as a Dex IP, else its brand."""
        if card in self._groups:
            return self._groups[card]
        head = card[1] if card[0] == 'product' else None
        ids = ([head] if head else []) + [i for i in sorted(self.members[card]) if i != head]
        found = None
        if head in self.dex_ip:
            found = self.dex_ip[head]
        else:
            tags = Counter((tag['id'], tag['name']) for tag in (self.dex_ip.get(i) for i in ids) if tag)
            if tags:
                ident, name = tags.most_common(1)[0][0]
                found = {'id': ident, 'name': name}
        if found:
            result = (f"dex:{found['id']}", found['name'])
        else:
            result = None
            for product_id in ids:
                product = self.products[product_id]
                if not (product['ip_series'] or '').strip():
                    continue
                named = suggest_ip(dict(product), {})
                hit = self.ip_names.get((named or product['ip_series']).strip().casefold())
                if hit:
                    result = (f'dex:{hit[0]}', hit[1])
                break
            if result is None:
                brand = next((self.products[i]['brand'] for i in ids if (self.products[i]['brand'] or '').strip()), '')
                result = _brand_key(brand)
        self._groups[card] = result
        return result


def _lines(con, store_id, catalog=None):
    """Every line of the store: sellable stock at one of its places, with its kind, group and card."""
    catalog = catalog or _Catalog(con)
    lines = []
    for row in con.execute(
            """SELECT b.product_id, b.quantity, l.id AS location_id, l.code, l.name FROM inventory_balances b
               JOIN inventory_locations l ON l.id=b.location_id
               WHERE l.store_id=? AND l.is_active=1 AND b.disposition='saleable' AND b.quantity>0""", (store_id,)):
        product = catalog.products.get(row['product_id'])
        if product is None:
            continue
        if row['code'] in BOXES_ONLY and product['stock_form'] == 'sealed_set':
            continue  # whole sets are counted upstairs; the floor card has none (stock_counting._here)
        card = catalog.card_of(row['product_id'])
        group, group_name = catalog.group(card)
        lines.append({'product_id': row['product_id'], 'quantity': row['quantity'], 'location_id': row['location_id'],
                      'place': row['code'], 'place_name': row['name'], 'kind': kind_of(row['code'], product),
                      'group': group, 'group_name': group_name, 'card': card})
    return lines, catalog


# The layout ---------------------------------------------------------------------------------------------

def _layout(con, store_id):
    sections = [dict(row) for row in con.execute(
        'SELECT id, name, sort_order FROM count_sections WHERE store_id=? AND retired_at IS NULL ORDER BY sort_order, id',
        (store_id,))]
    parts = {(row['kind'], row['group_key']): row['section_id'] for row in con.execute(
        'SELECT kind, group_key, section_id FROM count_section_parts WHERE store_id=?', (store_id,))}
    version = con.execute('SELECT version FROM count_section_layouts WHERE store_id=?', (store_id,)).fetchone()
    return sections, parts, version[0] if version else 0


def section_of(parts, kind, group):
    return parts.get((kind, group)) or parts.get((kind, STAR))


def _split(groups, bins):
    """Whole groups into `bins` parts of about the same size: the largest group into the emptiest part."""
    parts = [{'lines': 0, 'groups': []} for _ in range(bins)]
    for group, count in sorted(groups.items(), key=lambda item: (-item[1], item[0])):
        emptiest = min(parts, key=lambda part: part['lines'])
        emptiest['lines'] += count
        emptiest['groups'].append(group)
    return [part for part in parts if part['groups']]


def propose(lines, sections=SECTIONS):
    """About `sections` sections of about the same number of lines, kind by kind: a big kind is split
    by whole groups, a kind under half a section joins the smallest one."""
    by_kind = defaultdict(Counter)
    names = {}
    for line in lines:
        by_kind[line['kind']][line['group']] += 1
        names[line['group']] = line['group_name']
    total = sum(sum(groups.values()) for groups in by_kind.values())
    wanted = max(1, min(sections, sum(len(groups) for groups in by_kind.values())))
    target = total / wanted if total else 1
    share = {kind: (round(sum(groups.values()) / target) if sum(groups.values()) >= target / 2 else 0)
             for kind, groups in by_kind.items()}
    for kind in share:
        share[kind] = min(share[kind], len(by_kind[kind]))
    if total and not any(share.values()):
        share[max(by_kind, key=lambda kind: sum(by_kind[kind].values()))] = 1

    def per_section(kind):
        return sum(by_kind[kind].values()) / share[kind]
    while total and sum(share.values()) < wanted:
        room = [kind for kind in share if 0 < share[kind] < len(by_kind[kind])]
        if not room:
            break
        share[max(room, key=per_section)] += 1
    while sum(share.values()) > wanted:
        many = [kind for kind in share if share[kind] > 1]
        if many:
            share[min(many, key=per_section)] -= 1
        else:
            share[min((kind for kind in share if share[kind]), key=lambda kind: sum(by_kind[kind].values()))] = 0

    proposal = []
    for kind in KINDS:
        if not share.get(kind):
            continue
        split = _split(by_kind[kind], share[kind])
        star = max(range(len(split)), key=lambda index: split[index]['lines'])
        for index, part in enumerate(split):
            letter = f' {chr(65 + index)}' if len(split) > 1 else ''
            parts = ([{'kind': kind, 'group_key': group} for group in part['groups']] if len(split) > 1 else [])
            if index == star:
                parts.append({'kind': kind, 'group_key': STAR})
            proposal.append({'name': f'{KIND_NAMES[kind]}{letter}', 'parts': parts, 'lines': part['lines'],
                             'groups': [names[group] for group in part['groups']]})
    if not proposal:
        proposal.append({'name': KIND_NAMES['blind_box'], 'parts': [], 'lines': 0, 'groups': []})
    for kind in KINDS:
        if share.get(kind):
            continue
        # A small kind (or one with nothing yet) is counted with the smallest section.
        smallest = min(proposal, key=lambda section: section['lines'])
        smallest['parts'].append({'kind': kind, 'group_key': STAR})
        if by_kind.get(kind):
            smallest['lines'] += sum(by_kind[kind].values())
            smallest['name'] += f' + {KIND_NAMES[kind]}'
            smallest['groups'] += [names[group] for group in by_kind[kind]]
    return proposal


def _describe(con, store_id, lines, sections, parts, catalog):
    """Each section with what is in it now: lines, groups (kind by kind), new groups, last count."""
    split_kinds = {kind for kind, group in parts if group != STAR}
    held = {section['id']: {'lines': 0, 'groups': defaultdict(Counter), 'new': set()} for section in sections}
    loose = Counter()
    for line in lines:
        section_id = section_of(parts, line['kind'], line['group'])
        if section_id not in held:
            loose[line['kind']] += 1
            continue
        entry = held[section_id]
        entry['lines'] += 1
        entry['groups'][line['kind']][(line['group'], line['group_name'])] += 1
        if (line['kind'], line['group']) not in parts and line['kind'] in split_kinds:
            entry['new'].add((line['kind'], line['group']))
    last = {row['section_id']: dict(row) for row in con.execute(
        """SELECT sc.section_id, sc.id, sc.finished_at, sc.business_date, sc.counter_name FROM section_counts sc
           WHERE sc.store_id=? AND sc.finished_at IS NOT NULL
             AND sc.id=(SELECT MAX(x.id) FROM section_counts x WHERE x.section_id=sc.section_id
                        AND x.finished_at IS NOT NULL)""", (store_id,))}
    open_counts = {row['section_id']: dict(row) for row in con.execute(
        """SELECT section_id, id, started_at, counter_name FROM section_counts
           WHERE store_id=? AND finished_at IS NULL""", (store_id,))}
    total = sum(entry['lines'] for entry in held.values())
    average = total / len(sections) if sections else 0
    described = []
    for section in sections:
        entry = held[section['id']]
        kinds = [kind for kind in KINDS if any(k == kind and s == section['id'] for (k, _), s in parts.items())]
        described.append({
            **section, 'lines': entry['lines'], 'kinds': kinds,
            'groups': [{'kind': kind, 'group_key': key, 'name': name, 'lines': count,
                        'new': (kind, key) in entry['new']}
                       for kind in KINDS for (key, name), count in sorted(
                           entry['groups'][kind].items(), key=lambda item: (-item[1], item[0][1]))],
            'takes_new': [kind for kind in KINDS if parts.get((kind, STAR)) == section['id']],
            'uneven': bool(average and entry['lines'] > average * UNEVEN and len(sections) > 1),
            'last_count': last.get(section['id']), 'open_count': open_counts.get(section['id']),
        })
    return described, total, sum(loose.values())


def _due(described, today):
    """Next up: the section counted longest ago (never counted first); due two weeks after the last count."""
    if not described:
        return None
    def counted(section):
        return (section['last_count'] or {}).get('business_date') or ''
    upcoming = min(described, key=lambda section: (counted(section), section['sort_order'], section['id']))
    finished = [counted(section) for section in described if counted(section)]
    due_on = (date.fromisoformat(max(finished)) + timedelta(days=CYCLE_DAYS)).isoformat() if finished else today
    return {'section_id': upcoming['id'], 'name': upcoming['name'], 'last_counted': counted(upcoming) or None,
            'due_on': due_on, 'overdue': due_on < today}


def sections_overview(con, actor, store_code):
    def work():
        scope = _live_scope(con, actor, store_code)
        store_id = scope['store']['id']
        sections, parts, version = _layout(con, store_id)
        lines, catalog = _lines(con, store_id)
        described, total, loose = _describe(con, store_id, lines, sections, parts, catalog)
        groups = Counter((line['kind'], line['group'], line['group_name']) for line in lines)
        marks = con.execute("SELECT COUNT(*) FROM count_marks WHERE store_id=? AND status='open'",
                            (store_id,)).fetchone()[0]
        return {'store': scope['store'], 'version': version, 'sections': described, 'lines': total + loose,
                'outside': loose, 'next_up': _due(described, business_date()), 'cycle_days': CYCLE_DAYS,
                # 待盘点 count-next-time marks: the next section count that starts takes them all.
                'marks': marks,
                # Every (kind, group) with lines now, biggest first: what the editor places in sections.
                'groups': [{'kind': kind, 'group_key': key, 'name': name, 'lines': count}
                           for (kind, key, name), count in sorted(
                               groups.items(), key=lambda item: (KINDS.index(item[0][0]), -item[1], item[0][2]))],
                'today': business_date()}
    return _read(con, work)


def sections_proposal(con, actor, store_code):
    def work():
        scope = _live_scope(con, actor, store_code)
        lines, _ = _lines(con, scope['store']['id'])
        return {'store': scope['store'], 'lines': len(lines), 'sections': propose(lines)}
    return _read(con, work)


def _read_layout(data):
    raw = data.get('sections')
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_SECTIONS:
        raise InventoryValidationError(f'Send 1 to {MAX_SECTIONS} sections')
    sections, seen = [], set()
    for number, item in enumerate(raw):
        if not isinstance(item, dict):
            raise InventoryValidationError('Each section needs a name and its parts')
        name = ' '.join(str(item.get('name') or '').split())
        if not name or len(name) > 60:
            raise InventoryValidationError('Give each section a name of up to 60 characters')
        section_id = item.get('id')
        if section_id is not None:
            section_id = read_int(section_id, 'id', minimum=1)
        parts = item.get('parts')
        if not isinstance(parts, list):
            raise InventoryValidationError(f'{name}: parts must be a list')
        clean = []
        for part in parts:
            kind = (part or {}).get('kind') if isinstance(part, dict) else None
            group = str((part or {}).get('group_key') or '').strip() if isinstance(part, dict) else ''
            if kind not in KINDS or not group or len(group) > 120:
                raise InventoryValidationError(f'{name}: each part needs a kind and a group')
            if (kind, group) in seen:
                raise InventoryValidationError(f'{group} is in two sections; put it in one')
            seen.add((kind, group))
            clean.append((kind, group))
        sections.append({'id': section_id, 'name': name, 'sort_order': number, 'parts': clean})
    missing = [KIND_NAMES[kind] for kind in KINDS if (kind, STAR) not in seen]
    if missing:
        # Without a section for its new IPs a kind would leave products out of every section.
        raise InventoryValidationError(f"Pick the section that takes new products of {', '.join(missing)}",
                                       'sections_incomplete')
    return sections


def save_layout(con, actor, store_code, data):
    """Replace the store's sections. Sections left out are retired, keeping their history."""
    if not isinstance(data, dict):
        raise InventoryValidationError('Send the sections as JSON')
    expected = read_int(data.get('version'), 'version', minimum=0)
    sections = _read_layout(data)
    _begin(con)
    try:
        scope = _live_scope(con, actor, store_code)
        store_id = scope['store']['id']
        current = con.execute('SELECT version FROM count_section_layouts WHERE store_id=?', (store_id,)).fetchone()
        if (current[0] if current else 0) != expected:
            raise InventoryConflict('Someone else changed the sections while you were editing. Look at them again.',
                                    'sections_changed')
        mine = {row[0] for row in con.execute(
            'SELECT id FROM count_sections WHERE store_id=? AND retired_at IS NULL', (store_id,))}
        if any(section['id'] is not None and section['id'] not in mine for section in sections):
            raise InventoryConflict('A section was removed while you were editing. Look at them again.',
                                    'sections_changed')
        kept = set()
        for section in sections:
            if section['id'] is None:
                section['id'] = con.execute(
                    'INSERT INTO count_sections(store_id, name, sort_order, updated_by) VALUES (?, ?, ?, ?)',
                    (store_id, section['name'], section['sort_order'], actor['sub'])).lastrowid
            else:
                con.execute("""UPDATE count_sections SET name=?, sort_order=?, updated_by=?, updated_at=datetime('now')
                               WHERE id=?""", (section['name'], section['sort_order'], actor['sub'], section['id']))
            kept.add(section['id'])
        for retired in mine - kept:
            if con.execute('SELECT 1 FROM section_counts WHERE section_id=? AND finished_at IS NULL',
                           (retired,)).fetchone():
                raise InventoryConflict('A section being counted cannot be removed; finish its count first.',
                                        'section_count_open')
            con.execute("UPDATE count_sections SET retired_at=datetime('now'), updated_by=? WHERE id=?",
                        (actor['sub'], retired))
        con.execute('DELETE FROM count_section_parts WHERE store_id=?', (store_id,))
        con.executemany('INSERT INTO count_section_parts(store_id, kind, group_key, section_id) VALUES (?, ?, ?, ?)',
                        [(store_id, kind, group, section['id']) for section in sections for kind, group in section['parts']])
        con.execute("""INSERT INTO count_section_layouts(store_id, version, updated_by, updated_at)
                       VALUES (?, 1, ?, datetime('now'))
                       ON CONFLICT(store_id) DO UPDATE SET version=version+1, updated_by=excluded.updated_by,
                           updated_at=excluded.updated_at""", (store_id, actor['sub']))
        con.commit()
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
    return sections_overview(con, actor, store_code)


# Section counts -----------------------------------------------------------------------------------------

def _section_count(con, section_count_id):
    row = con.execute(
        """SELECT sc.*, s.code AS store_code FROM section_counts sc JOIN stores s ON s.id=sc.store_id
           WHERE sc.id=?""", (read_int(section_count_id, 'section_count_id', minimum=1),)).fetchone()
    if row is None:
        raise InventoryError('Section count not found', 'not_found', 404)
    return row


def start_count(con, actor, store_code, data):
    if not isinstance(data, dict):
        raise InventoryValidationError('Send the section and the counter as JSON')
    section_id = read_int(data.get('section_id'), 'section_id', minimum=1)
    employee_id = read_int(data.get('counter_employee_id'), 'counter_employee_id', minimum=1)
    _begin(con)
    try:
        scope = _live_scope(con, actor, store_code)
        store_id = scope['store']['id']
        section = con.execute('SELECT id, name FROM count_sections WHERE id=? AND store_id=? AND retired_at IS NULL',
                              (section_id, store_id)).fetchone()
        if section is None:
            raise InventoryError('Section not found', 'not_found', 404)
        employee = con.execute('SELECT id, name FROM employees WHERE id=? AND is_active=1', (employee_id,)).fetchone()
        if employee is None:
            raise InventoryValidationError('Pick who is counting', 'counter_unknown')
        open_count = con.execute('SELECT id FROM section_counts WHERE section_id=? AND finished_at IS NULL',
                                 (section_id,)).fetchone()
        if open_count:
            error = InventoryConflict(f"{section['name']} is already being counted. Carry on with that count.",
                                      'section_count_open')
            error.details = {'section_count_id': open_count[0]}
            raise error
        _, parts, _ = _layout(con, store_id)
        lines, catalog = _lines(con, store_id)
        mine = [line for line in lines if section_of(parts, line['kind'], line['group']) == section_id]
        _add_marks(con, store_id, lines, mine, catalog)
        count_id = con.execute(
            """INSERT INTO section_counts(store_id, section_id, section_name, counter_employee_id, counter_name,
                                          business_date, started_by) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (store_id, section_id, section['name'], employee['id'], employee['name'] or 'someone', business_date(),
             actor['sub'])).lastrowid
        con.executemany(
            """INSERT INTO section_count_lines(section_count_id, location_id, product_id, card_product_id, kind,
                                               book_quantity, mark_id) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(count_id, line['location_id'], line['product_id'], catalog.opens(line['card']), line['kind'],
              line['quantity'], line.get('mark_id')) for line in mine])
        con.commit()
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
    return count_view(con, actor, count_id)


def _add_marks(con, store_id, lines, mine, catalog):
    """待盘点: the store's open marks join the section count's lines, also from another section, and also where
    POPCORE shows nothing (the count is how that gets checked)."""
    every = {(line['location_id'], line['product_id']): line for line in lines}
    held = {(line['location_id'], line['product_id']): line for line in mine}
    for row in con.execute(
            """SELECT m.id, m.location_id, m.product_id, l.code, l.name FROM count_marks m
               JOIN inventory_locations l ON l.id=m.location_id
               WHERE m.store_id=? AND m.status='open' AND l.is_active=1 ORDER BY m.id""", (store_id,)).fetchall():
        key = (row['location_id'], row['product_id'])
        if key in held:
            held[key]['mark_id'] = row['id']
            continue
        product = catalog.products.get(row['product_id'])
        if product is None:
            continue
        line = dict(every[key]) if key in every else None
        if line is None:
            balance = con.execute("""SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?
                                     AND disposition='saleable'""", key[::-1]).fetchone()
            line = {'product_id': row['product_id'], 'quantity': balance[0] if balance else 0,
                    'location_id': row['location_id'], 'place': row['code'], 'place_name': row['name'],
                    'kind': kind_of(row['code'], product), 'card': catalog.card_of(row['product_id'])}
        line['mark_id'] = row['id']
        mine.append(line)
        held[key] = line


def _counted(con, section_count_id):
    """(location, product) → the latest count line saved in this section count."""
    return {(row['location_id'], row['product_id']): dict(row) for row in con.execute(
        """SELECT c.location_id, line.product_id, line.expected_quantity, line.observed_quantity, c.created_by,
                  c.created_at, c.id AS count_id FROM section_count_saves link
           JOIN inventory_counts c ON c.id=link.inventory_count_id
           JOIN inventory_count_lines line ON line.count_id=c.id
           WHERE link.section_count_id=? ORDER BY c.id, line.line_no""", (section_count_id,))}


def _roles(section_kinds, place):
    if place == 'display':
        return None
    roles = sorted({role for kind in section_kinds for role in KIND_ROLES.get(kind, ())})
    return roles or None


def count_view(con, actor, section_count_id):
    """A section count: its cards place by place, each with how many of its lines are counted."""
    def work():
        record = _section_count(con, section_count_id)
        scope = _live_scope(con, actor, record['store_code'])
        places = {location['id']: location for location in scope['locations']}
        counted = _counted(con, record['id'])
        cards = {}
        for row in con.execute(
                f"""SELECT line.*, {LABEL_SQL} AS label, p.sku, m.reason AS mark_reason,
                           (SELECT COALESCE(NULLIF(mp.design_name, ''), NULLIF(mp.jizhanming, ''),
                                            NULLIF(mp.name_cn_en, ''), mp.sku)
                            FROM products mp WHERE mp.id=line.product_id) AS marked_name
                    FROM section_count_lines line JOIN products p ON p.id=line.card_product_id
                    LEFT JOIN count_marks m ON m.id=line.mark_id
                    WHERE line.section_count_id=? ORDER BY line.location_id, label COLLATE NOCASE""",
                (record['id'],)):
            key = (row['location_id'], row['card_product_id'])
            card = cards.setdefault(key, {'product_id': row['card_product_id'], 'title': row['label'],
                                          'sku': row['sku'], 'lines': 0, 'counted': 0, 'kinds': set(), 'added': False,
                                          'image': None, 'marks': []})
            card['lines'] += 1
            card['kinds'].add(row['kind'])
            card['added'] = card['added'] or bool(row['added'])
            card['counted'] += (row['location_id'], row['product_id']) in counted
            if row['mark_id']:
                # 待盘点: which item of the card was marked, and why; the card is listed first.
                card['marks'].append({'product_id': row['product_id'], 'name': row['marked_name'],
                                      'reason': row['mark_reason'],
                                      'counted': (row['location_id'], row['product_id']) in counted})
        by_place = defaultdict(list)
        for (location_id, _), card in cards.items():
            place = places.get(location_id) or {'id': location_id, 'code': '', 'name': ''}
            card['roles'] = _roles(card.pop('kinds'), place['code'])
            card['image'] = _image(con, card['product_id'])
            by_place[location_id].append(card)
        ordered = sorted(by_place, key=lambda location_id: (_placed((places.get(location_id) or {}).get('code')), location_id))
        names = _names(con, [record['started_by'], record['finished_by']])
        return {
            'id': record['id'], 'store': scope['store'], 'section': {'id': record['section_id'], 'name': record['section_name']},
            'counter': record['counter_name'], 'business_date': record['business_date'],
            'started_at': record['started_at'], 'started_by': names.get(record['started_by']) or 'someone',
            'finished_at': record['finished_at'],
            'finished_by': names.get(record['finished_by']) or ('someone' if record['finished_by'] else None),
            'places': [{'id': location_id, 'code': (places.get(location_id) or {}).get('code'),
                        'name': (places.get(location_id) or {}).get('name'), 'cards': by_place[location_id]}
                       for location_id in ordered],
            'lines': sum(card['lines'] for card in cards.values()),
            'counted': sum(card['counted'] for card in cards.values()),
        }
    return _read(con, work)


def link_save(con, section_count_id, location_id, count_id, product_ids):
    """Called by stock_counting inside a live save: keep the save with its section count, and add
    to its list what was counted here although it was not on the list (found on the shelf)."""
    con.execute('INSERT INTO section_count_saves(section_count_id, inventory_count_id) VALUES (?, ?)',
                (section_count_id, count_id))
    catalog = None
    for product_id in product_ids:
        if con.execute('SELECT 1 FROM section_count_lines WHERE section_count_id=? AND location_id=? AND product_id=?',
                       (section_count_id, location_id, product_id)).fetchone():
            continue
        catalog = catalog or _Catalog(con)
        place = con.execute('SELECT code FROM inventory_locations WHERE id=?', (location_id,)).fetchone()[0]
        product = catalog.products[product_id]
        con.execute(
            """INSERT INTO section_count_lines(section_count_id, location_id, product_id, card_product_id, kind,
                                               book_quantity, added) VALUES (?, ?, ?, ?, ?, 0, 1)""",
            (section_count_id, location_id, product_id, catalog.opens(catalog.card_of(product_id)),
             kind_of(place, product)))


def open_count_for_save(con, section_count_id, store_id):
    """The open section count a 盘点 save names, at the same store."""
    record = _section_count(con, section_count_id)
    if record['store_id'] != store_id:
        raise InventoryValidationError('This section count is for another store', 'section_count_store')
    if record['finished_at']:
        raise InventoryConflict(f"The count of {record['section_name']} is finished. Start a new one to count again.",
                                'section_count_closed')
    return record


def summary(con, actor, section_count_id):
    """What the section count found: every line, differences first, with who counted and who entered."""
    def work():
        record = _section_count(con, section_count_id)
        scope = _live_scope(con, actor, record['store_code'])
        return _summary(con, record, scope)
    return _read(con, work)


def _summary(con, record, scope):
    places = {location['id']: location for location in scope['locations']}
    counted = _counted(con, record['id'])
    names = _names(con, [row['created_by'] for row in counted.values()]
                   + [record['started_by'], record['finished_by']])
    rows = []
    for line in con.execute(
            f"""SELECT line.*, {LABEL_SQL} AS label, p.sku, p.price, p.design_name
                FROM section_count_lines line JOIN products p ON p.id=line.product_id
                WHERE line.section_count_id=?""", (record['id'],)):
        found = counted.get((line['location_id'], line['product_id']))
        place = places.get(line['location_id']) or {'code': '', 'name': ''}
        row = {'product_id': line['product_id'], 'card_product_id': line['card_product_id'], 'sku': line['sku'],
               'name': line['label'], 'place': place['code'], 'place_name': place['name'],
               'added': bool(line['added']), 'marked': bool(line['mark_id']), 'price': line['price']}
        if found:
            difference = found['observed_quantity'] - found['expected_quantity']
            row.update({'expected': found['expected_quantity'], 'counted': found['observed_quantity'],
                        'difference': difference,
                        'value': round(difference * line['price'], 2) if line['price'] is not None else None,
                        'entered_by': names.get(found['created_by']) or 'someone', 'entered_at': found['created_at']})
        else:
            row.update({'expected': line['book_quantity'], 'counted': None, 'difference': None, 'value': None})
        rows.append(row)
    rows.sort(key=lambda row: (row['counted'] is None, row['difference'] in (0, None), _placed(row['place']),
                               row['name'].casefold()))
    differences = [row for row in rows if row['difference']]
    return {
        'id': record['id'], 'store': scope['store'], 'section': {'id': record['section_id'], 'name': record['section_name']},
        'counter': record['counter_name'], 'business_date': record['business_date'],
        'started_at': record['started_at'], 'started_by': names.get(record['started_by']) or 'someone',
        'finished_at': record['finished_at'],
        'finished_by': names.get(record['finished_by']) or ('someone' if record['finished_by'] else None),
        'lines': len(rows), 'counted': sum(1 for row in rows if row['counted'] is not None),
        'more': sum(row['difference'] for row in differences if row['difference'] > 0),
        'less': -sum(row['difference'] for row in differences if row['difference'] < 0),
        'value': round(sum(row['value'] or 0 for row in differences), 2),
        'rows': rows,
    }


def finish_count(con, actor, section_count_id):
    _begin(con)
    try:
        record = _section_count(con, section_count_id)
        scope = _live_scope(con, actor, record['store_code'])
        if not record['finished_at']:
            con.execute("UPDATE section_counts SET finished_at=datetime('now'), finished_by=? WHERE id=?",
                        (actor['sub'], record['id']))
            record = _section_count(con, record['id'])
        result = _summary(con, record, scope)
        con.commit()
        return result
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def counters(con, actor, store_code):
    """Who may be picked as the counter: active staff, this store's first."""
    def work():
        scope = _live_scope(con, actor, store_code)
        return {'employees': [{'employee_id': row['id'], 'name': row['name'] or f"#{row['id']}", 'here': bool(row['here'])}
                              for row in con.execute(
                                  """SELECT e.id, e.name, EXISTS (SELECT 1 FROM employee_stores es
                                                                  WHERE es.employee_id=e.id AND es.store_id=?) AS here
                                     FROM employees e WHERE e.is_active=1
                                     ORDER BY here DESC, e.name COLLATE NOCASE, e.id""", (scope['store']['id'],))]}
    return _read(con, work)


def history(con, actor, store_code, limit=30):
    def work():
        scope = _live_scope(con, actor, store_code)
        rows = [dict(row) for row in con.execute(
            """SELECT sc.id, sc.section_id, sc.section_name, sc.counter_name, sc.business_date, sc.started_at,
                      sc.finished_at,
                      (SELECT COUNT(*) FROM section_count_lines l WHERE l.section_count_id=sc.id) AS lines
               FROM section_counts sc WHERE sc.store_id=? ORDER BY sc.id DESC LIMIT ?""",
            (scope['store']['id'], limit))]
        return {'counts': rows}
    return _read(con, work)
