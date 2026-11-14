"""图鉴 Dex: apply the owner's review decisions to the book (docs/dex-book.md, "Applying review decisions").

A decisions file is an ordered list of operations in a small fixed vocabulary:

- create: kind, parent, name, optional blind_box, rarity, partner, character, ip, extras, and a key that later
  operations can refer to;
- set: an entry and the fields to change (name, partner, character, ip, blind_box, rarity, extras);
- move: an entry and its new parent;
- delete: an entry, with everything under it;
- link / unlink: a POPCORE ID and an entry.

Each operation carries why: the card or decision it comes from and a plain reason. An entry is referred to by
kind + website number + item flag ({"kind", "website_id", "item"}), by the key of an earlier create ({"key"}), or,
for entries with no website number, by kind + name + parent ({"kind", "name", "parent"}; names match in any case).

Every write goes through dex_book's own functions, as the actor owner-review, so every rule holds. All operations
run inside ONE transaction (dex_book.batch): a dry run rolls it back; an apply commits it only when every
operation could be done, and otherwise writes nothing. An operation whose result already holds is reported
"already done" and changes nothing, so running a file twice is safe. A create is found again by its kind, parent
and name.
"""
import json

import dex_book
from dex_book import DexError

ACTOR = {'sub': 'owner-review'}
OPS = ('create', 'set', 'move', 'delete', 'link', 'unlink')
SET_FIELDS = ('name', 'partner', 'character', 'ip', 'blind_box', 'rarity', 'extras')
REF_FIELDS = {'partner': 'partner_id', 'character': 'character_id', 'ip': 'ip_id'}
OP_KEYS = {
    'create': {'op', 'why', 'key', 'kind', 'parent', 'name', 'partner', 'character', 'ip', 'blind_box', 'rarity', 'extras'},
    'set': {'op', 'why', 'entry', 'fields'},
    'move': {'op', 'why', 'entry', 'parent'},
    'delete': {'op', 'why', 'entry'},
    'link': {'op', 'why', 'entry', 'popcore_id'},
    'unlink': {'op', 'why', 'entry', 'popcore_id'},
}
DONE, WOULD, ALREADY, REFUSED = 'done', 'would do', 'already done', 'refused'


class Refused(Exception):
    """This operation cannot be done; the reason is shown in the report."""


def read_decisions(path):
    with open(path, encoding='utf-8') as handle:
        document = json.load(handle)
    operations = document.get('operations') if isinstance(document, dict) else document
    if not isinstance(operations, list):
        raise DexError('A decisions file is a list of operations (or {"operations": [...]}).', 'invalid_input')
    return operations


# Referring to entries -------------------------------------------------------------------------------------

def describe(ref):
    if not isinstance(ref, dict):
        return repr(ref)
    if 'key' in ref:
        return f'new entry “{ref["key"]}”'
    if 'website_id' in ref:
        return f'{ref.get("kind")} {"item " if ref.get("item") else ""}#{ref["website_id"]}'
    parent = ref.get('parent')
    return f'{ref.get("kind")} “{ref.get("name")}”' + (f' under {describe(parent)}' if parent else '')


def _row(con, entry_id):
    row = con.execute('SELECT * FROM dex_entries WHERE id=?', (entry_id,)).fetchone()
    return dict(row) if row else None


def resolve(con, ref, keys, deleted_too=False):
    """The dex_entries row a reference names (a dict), or Refused. deleted_too: a deleted row is returned too."""
    if not isinstance(ref, dict):
        raise Refused('An entry is referred to by an object.')
    if 'key' in ref:
        if set(ref) != {'key'}:
            raise Refused('A reference by key holds only the key.')
        if ref['key'] not in keys:
            raise Refused(f'No earlier create made “{ref["key"]}” (it is missing, later in the file, or was refused).')
        row = _row(con, keys[ref['key']])
        if row is None or (row['deleted_at'] is not None and not deleted_too):
            raise Refused(f'The entry made as “{ref["key"]}” is no longer in the book.')
        return row
    kind = ref.get('kind')
    if kind not in dex_book.KINDS:
        raise Refused(f'kind is one of {", ".join(dex_book.KINDS)}.')
    if 'website_id' in ref:
        if set(ref) - {'kind', 'website_id', 'item'}:
            raise Refused('A reference by website number holds kind, website_id and item.')
        number = ref['website_id']
        if isinstance(number, bool) or not isinstance(number, int):
            raise Refused('website_id is a number.')
        row = con.execute('SELECT * FROM dex_entries WHERE kind=? AND website_id=? AND website_item=?',
                          (kind, number, 1 if ref.get('item') else 0)).fetchone()
        if row is None:
            raise Refused(f'No {describe(ref)} in the book.')
        if row['deleted_at'] is not None and not deleted_too:
            raise Refused(f'{describe(ref)} is deleted.')
        return dict(row)
    if 'name' in ref:
        if set(ref) - {'kind', 'name', 'parent'}:
            raise Refused('A reference by name holds kind, name and parent.')
        parent = ref.get('parent')
        parent_id = None if parent is None else resolve(con, parent, keys)['id']
        name = ' '.join(str(ref['name'] or '').split()).casefold()
        found = [dict(row) for row in con.execute(
            'SELECT * FROM dex_entries WHERE kind=? AND parent_id IS ? AND deleted_at IS NULL', (kind, parent_id))
            if row['name'].casefold() == name]
        if not found:
            raise Refused(f'No {describe(ref)} in the book.')
        if len(found) > 1:
            raise Refused(f'Several entries match {describe(ref)}.')
        return found[0]
    raise Refused('An entry is referred to by key, by kind + website_id + item, or by kind + name + parent.')


def _item(con, popcore_id):
    if not isinstance(popcore_id, str) or not popcore_id.strip():
        raise Refused('popcore_id is a POPCORE ID such as PM0545.')
    row = con.execute('SELECT id, sku FROM products WHERE sku=? COLLATE NOCASE', (popcore_id.strip(),)).fetchone()
    if row is None:
        raise Refused(f'No POPCORE item {popcore_id}.')
    return row


def _sku(con, product_id):
    row = con.execute('SELECT sku FROM products WHERE id=?', (product_id,)).fetchone()
    return row[0] if row else f'item {product_id}'


# The operations -------------------------------------------------------------------------------------------

def _fields(con, fields, keys):
    """Decision fields → dex_book fields (references become ids)."""
    if not isinstance(fields, dict) or not fields:
        raise Refused('fields is an object with at least one of ' + ', '.join(SET_FIELDS) + '.')
    unknown = sorted(set(fields) - set(SET_FIELDS))
    if unknown:
        raise Refused(f'Unknown fields: {", ".join(unknown)}.')
    out = {}
    for field, value in fields.items():
        if field in REF_FIELDS:
            out[REF_FIELDS[field]] = None if value is None else resolve(con, value, keys)['id']
        else:
            out[field] = value
    return out


def _holds(entry, wanted):
    """True when the live entry already has every wanted value."""
    for field, value in wanted.items():
        have = entry.get(field)
        if field == 'name':
            if have != ' '.join(str(value or '').split()):
                return False
        elif field == 'blind_box':
            if (None if value is None else bool(value)) != have:
                return False
        elif field == 'extras':
            if not isinstance(value, dict) or any(have.get(k) != v for k, v in value.items()):
                return False
        elif have != value:
            return False
    return True


def _change(con, entry_id, wanted):
    """Update through dex_book; True when something changed."""
    entry = dex_book._live(con, entry_id)
    if _holds(entry, wanted):
        return False
    dex_book.update(con, entry_id, wanted, ACTOR)
    return True


def _create(con, op, keys):
    key = op.get('key')
    if key is not None and (not isinstance(key, str) or not key):
        raise Refused('key is a text.')
    if key in keys:
        raise Refused(f'The key “{key}” is used by an earlier create.')
    kind = op.get('kind')
    if kind not in dex_book.KINDS:
        raise Refused(f'kind is one of {", ".join(dex_book.KINDS)}.')
    parent = op.get('parent')
    parent_id = None if parent is None else resolve(con, parent, keys)['id']
    name = ' '.join(str(op.get('name') or '').split())
    if not name:
        raise Refused('A name is required.')
    fields = {field: op[field] for field in SET_FIELDS if field in op and field != 'name'}
    wanted = _fields(con, fields, keys) if fields else {}
    same = [dict(row) for row in con.execute(
        'SELECT id, name, updated_by FROM dex_entries WHERE kind=? AND parent_id IS ? AND deleted_at IS NULL',
        (kind, parent_id)) if row['name'].casefold() == name.casefold()]
    if same:
        found = same[0]
        if found['updated_by'] != ACTOR['sub']:
            raise Refused(f'There is already a {kind} named “{found["name"]}” here (made by {found["updated_by"] or "a person"}); '
                          'refer to it instead of making it.')
        if key:
            keys[key] = found['id']
        changed = _change(con, found['id'], wanted) if wanted else False
        return found['id'], changed, f'{kind} “{found["name"]}” (#{found["id"]}) is already in the book' + \
            (', its fields brought in line' if changed else '')
    entry = dex_book.create(con, {'kind': kind, 'parent_id': parent_id, 'name': name, **wanted}, ACTOR)
    if key:
        keys[key] = entry['id']
    return entry['id'], True, f'make {kind} “{entry["name"]}” (#{entry["id"]})'


def _set(con, op, keys):
    entry = resolve(con, op.get('entry'), keys)
    wanted = _fields(con, op.get('fields'), keys)
    before = dex_book._live(con, entry['id'])
    changed = _change(con, entry['id'], wanted)
    shown = ', '.join(f'{field} {before.get(field)!r} → {value!r}' for field, value in wanted.items()
                      if not _holds(before, {field: value}))
    return entry['id'], changed, (f'{entry["kind"]} “{entry["name"]}” (#{entry["id"]}): {shown}' if changed
                                  else f'{entry["kind"]} “{entry["name"]}” (#{entry["id"]}) already has these values')


def _move(con, op, keys):
    entry = resolve(con, op.get('entry'), keys)
    if op.get('parent') is None:
        raise Refused('A move needs the new parent.')
    parent = resolve(con, op['parent'], keys)
    what = f'{entry["kind"]} “{entry["name"]}” (#{entry["id"]})'
    if entry['parent_id'] == parent['id']:
        return entry['id'], False, f'{what} is already under {parent["kind"]} “{parent["name"]}”'
    dex_book.update(con, entry['id'], {'parent_id': parent['id']}, ACTOR)
    return entry['id'], True, f'move {what} under {parent["kind"]} “{parent["name"]}” (#{parent["id"]})'


def _delete(con, op, keys):
    ref = op.get('entry')
    try:
        entry = resolve(con, ref, keys, deleted_too=True)
    except Refused:
        if isinstance(ref, dict) and 'name' in ref and 'website_id' not in ref:
            return None, False, f'{describe(ref)} is not in the book'  # a row with no website number is removed outright
        raise
    what = f'{entry["kind"]} “{entry["name"]}” (#{entry["id"]})'
    if entry['deleted_at'] is not None:
        return entry['id'], False, f'{what} is already deleted'
    result = dex_book.delete(con, entry['id'], ACTOR)
    counts = ', '.join(f'{count} {kind}' for kind, count in sorted(result['counts'].items()))
    return entry['id'], True, f'delete {what} with everything under it ({counts})'


def _link(con, op, keys):
    entry = resolve(con, op.get('entry'), keys)
    item = _item(con, op.get('popcore_id'))
    what = f'{entry["kind"]} “{entry["name"]}” (#{entry["id"]})'
    if entry['product_id'] == item['id']:
        return entry['id'], False, f'{what} already links {item["sku"]}'
    if entry['product_id'] is not None:
        raise Refused(f'{what} links {_sku(con, entry["product_id"])}; unlink it first.')
    dex_book.update(con, entry['id'], {'product_id': item['id']}, ACTOR)
    return entry['id'], True, f'link {item["sku"]} to {what}'


def _unlink(con, op, keys):
    entry = resolve(con, op.get('entry'), keys)
    item = _item(con, op.get('popcore_id'))
    what = f'{entry["kind"]} “{entry["name"]}” (#{entry["id"]})'
    if entry['product_id'] != item['id']:
        now = 'nothing' if entry['product_id'] is None else _sku(con, entry['product_id'])
        return entry['id'], False, f'{what} does not link {item["sku"]} (it links {now})'
    dex_book.update(con, entry['id'], {'product_id': None}, ACTOR)
    return entry['id'], True, f'unlink {item["sku"]} from {what}'


RUN = {'create': _create, 'set': _set, 'move': _move, 'delete': _delete, 'link': _link, 'unlink': _unlink}


def _check_shape(op):
    if not isinstance(op, dict):
        raise Refused('An operation is an object.')
    kind = op.get('op')
    if kind not in OPS:
        raise Refused(f'op is one of {", ".join(OPS)}.')
    unknown = sorted(set(op) - OP_KEYS[kind])
    if unknown:
        raise Refused(f'A {kind} has no {", ".join(unknown)}.')
    why = op.get('why')
    if not why or not isinstance(why, (str, dict)):
        raise Refused('Every operation says why (the card or decision, and a plain reason).')
    if kind in ('link', 'unlink') and 'popcore_id' not in op:
        raise Refused(f'A {kind} names the POPCORE ID.')
    if kind != 'create' and 'entry' not in op:
        raise Refused(f'A {kind} names its entry.')


def _why(op):
    why = op.get('why') if isinstance(op, dict) else None
    if isinstance(why, dict):
        return why.get('card') or why.get('decision') or '', why.get('reason') or ''
    return '', why or ''


def run(con, operations, apply=False):
    """Run every operation inside one transaction. A dry run (apply=False) rolls it back. apply=True commits only
    when no operation was refused; otherwise it rolls back and nothing is written. Returns the report."""
    if con.in_transaction:
        con.commit()
    con.execute('BEGIN IMMEDIATE')
    keys, report, counts = {}, [], {}
    try:
        with dex_book.batch(con):
            for number, op in enumerate(operations, start=1):
                card, reason = _why(op)
                line = {'n': number, 'op': op.get('op') if isinstance(op, dict) else None, 'card': card, 'reason': reason,
                        'entry': describe(op.get('entry') if isinstance(op, dict) and 'entry' in op
                                          else {'kind': op.get('kind'), 'name': op.get('name'), 'parent': op.get('parent')}
                                          if isinstance(op, dict) else op),
                        'popcore_id': op.get('popcore_id') if isinstance(op, dict) else None}
                con.execute('SAVEPOINT dex_op')
                try:
                    _check_shape(op)
                    entry_id, changed, what = RUN[op['op']](con, op, keys)
                    con.execute('RELEASE dex_op')
                    line.update(status=(DONE if apply else WOULD) if changed else ALREADY, result=what, entry_id=entry_id)
                except (Refused, DexError) as exc:
                    con.execute('ROLLBACK TO dex_op')
                    con.execute('RELEASE dex_op')
                    code = getattr(exc, 'code', 'refused')
                    line.update(status=REFUSED, result=str(exc), code=code, entry_id=None)
                report.append(line)
                counts.setdefault(line['op'] or '?', {}).setdefault(line['status'], 0)
                counts[line['op'] or '?'][line['status']] += 1
        refused = [line for line in report if line['status'] == REFUSED]
        written = apply and not refused
        if written:
            con.commit()
        else:
            con.rollback()
    except Exception:
        con.rollback()
        raise
    return {'applied': written, 'dry_run': not apply, 'operations': len(report), 'counts': counts,
            'refused': refused, 'report': report}
