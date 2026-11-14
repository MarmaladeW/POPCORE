"""图鉴 Dex step 2: the one-time load of the website's book into dex_entries (owner, 2026-11-12: "The Dex itself
lives on POPCORE"; dropped IPs are deleted for good; an outside name is a partner, never an IP under a toy maker).

plan(con, export, ip_list, evidence) reads the website's book export (contract section 5), the owner's checked IP list
and, optionally, earlier linking work (read_evidence: claims that a POPCORE ID is a given book entry), and
says what a load would do, without writing: rows to add, update, leave alone or store as deleted, the links to
POPCORE items it would make, and everything it needs the owner to look at. apply(con, plan) does it in one
transaction. Every row it writes passes dex_book's own checks (dex_book._check) and carries updated_by='load', which
is how a re-run tells its own rows from rows a person changed. The rules are in docs/dex-book.md, "The load".
"""
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict

from rapidfuzz import fuzz

import dex_book
from dex_book import DexError

LOAD = 'load'
ORDER = ('company', 'partner', 'ip', 'character', 'series', 'product', 'design')
# The website's fixed manufacturer codes (popcore_dex_manufacturers) and the IP list's company names.
MAKERS = {'pop_mart': 'POP MART', 'dreams_inc': 'Dreams Inc.', 'top_toy': 'TOP TOY', 'finding_unicorn': 'Finding Unicorn',
          'fun': 'Finding Unicorn', 'heyone': 'Heyone', 'trend_memo': 'Trend MEMO', 'tnt_space': 'TNT SPACE'}
RARITY_WORDS = {'regular': 'normal', 'mega_secret': 'super_secret'}
# POPCORE's own group words (products.ip_series) that are not the IP's name, for the suggestion on "not paired".
GROUP_WORDS = {'labubu': 'THE MONSTERS', '娃三岁': 'Baby Three', '迪士尼': 'Disney', '哈利波特': 'Harry Potter',
               '漫威 marvel': 'Marvel', 'popmart': 'POP MART'}
CLOSE = 85  # rapidfuzz ratio: at or above it, two names that are not equal are "close"
LINKABLE = ('random_box', 'ordinary', 'confirmed_design', None)


# Reading the two inputs ----------------------------------------------------------------------------------

def read_export(path):
    with open(path, encoding='utf-8') as handle:
        document = json.load(handle)
    if not isinstance(document, dict) or not isinstance(document.get('entries'), list):
        raise DexError('The export is not {"made_at": ..., "entries": [...]}.', 'invalid_input')
    return document


def read_ip_list(path):
    with open(path, encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    out = []
    for number, row in enumerate(rows, start=2):
        cell = (row.get('IP (and English name if different)') or '').strip()
        left, _, right = cell.partition(' = ')
        proper = re.sub(r'\s*\([^)]*\)\s*$', '', right).strip() or left.strip()
        out.append({'line': number, 'cell': cell, 'name': left.strip(), 'proper': proper,
                    'company': (row.get('Checked company') or '').strip(),
                    'what': (row.get('What it is') or '').strip(), 'was_under': (row.get('Was listed under') or '').strip(),
                    'decision': (row.get('Owner decision') or '').strip(), 'note': (row.get('Note') or '').strip()})
    return out


EVIDENCE_COLUMNS = ('popcore_id', 'entry_kind', 'website_id', 'item', 'series_slug', 'source', 'date', 'confidence',
                    'note')


def read_evidence(path):
    """Earlier linking work: one row per claim that a POPCORE ID is a book entry (docs/dex-book.md, "The load")."""
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in EVIDENCE_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise DexError(f'The evidence file lacks the columns {", ".join(missing)}.', 'invalid_input')
        rows = list(reader)
    out = []
    for number, row in enumerate(rows, start=2):
        kind = (row['entry_kind'] or '').strip()
        level = (row['confidence'] or '').strip().casefold()
        sku = (row['popcore_id'] or '').strip().upper()
        wid = (row['website_id'] or '').strip()
        item = (row['item'] or '').strip().casefold()
        if kind not in ('product', 'design') or level not in ('high', 'medium') or not sku or \
                (wid and not wid.isdigit()) or item not in ('', 'true', 'false'):
            raise DexError(f'Evidence line {number} is not a claim this load reads.', 'invalid_input')
        out.append({'line': number, 'sku': sku, 'kind': kind, 'website_id': int(wid) if wid else None,
                    'item': kind == 'product' and item == 'true', 'series_slug': (row['series_slug'] or '').strip(),
                    'source': (row['source'] or '').strip() or 'evidence', 'date': (row['date'] or '').strip(),
                    'confidence': level, 'note': (row['note'] or '').strip()})
    return out


def _real_company(name):
    return bool(name) and not name.startswith('(') and name.casefold() != 'not found'


def _rename(decision):
    for part in decision.split(';'):
        found = re.search(r'\brenamed?\s+(?:to\s+)?(.+?)(?:\s+\d{1,2}\s+[A-Z][a-z]{2})?\s*$', part.strip(), re.I)
        if found:
            return found.group(1).strip()
    return None


def ip_role(row):
    """What the owner's list makes of a row: drop, not_ip, partner or ip (a blank decision keeps it)."""
    decision = row['decision'].casefold()
    what = row['what'].casefold()
    if decision.startswith('delete'):
        return 'drop'
    if decision.startswith('not an ip'):
        return 'not_ip'
    if decision.startswith('collaboration'):
        return 'partner'
    if what == 'collaboration partner':
        return 'partner'
    if what == 'not an ip':
        return 'not_ip'
    return 'ip'


def norm(text):
    text = unicodedata.normalize('NFKC', text or '').casefold().replace('×', 'x')
    return re.sub(r'[^\w]+', '', text)


def _clean_extras(kind, extras):
    keep = {}
    for key in dex_book.EXTRAS[kind]:
        value = (extras or {}).get(key)
        if value is None or value == '' or value == []:
            continue
        keep[key] = value
    return keep


# The plan ------------------------------------------------------------------------------------------------

class _Plan:
    def __init__(self):
        self.rows = {}         # key -> planned row
        self.flags = []        # unsure: the owner's pick
        self.unmatched = []    # names on one side only
        self.dropped = []      # IP rows the website should delete
        self.paired = []
        self.notes = []

    def flag(self, what, key, name, why, choices='', website_id=None, full=False):
        """key: the planned row the flag is about (its place is filled in at the end), or None."""
        self.flags.append({'what': what, 'key': key, 'full': full, 'name': name, 'website_id': website_id,
                           'why': why, 'choices': choices})


def plan(con, export, ip_list, evidence=None):
    """What a load would do. Reads only. evidence: read_evidence() claims, or None."""
    p = _Plan()
    entries = [entry for entry in export['entries'] if isinstance(entry, dict)]
    by_kind = defaultdict(list)
    for entry in entries:
        by_kind[entry.get('kind')].append(entry)
    for kind in set(by_kind) - set(ORDER):
        p.notes.append(f'{len(by_kind[kind])} export rows of an unknown kind "{kind}" were skipped.')

    # Companies and partners from the owner's list ------------------------------------------------------
    companies = {}  # casefold name -> key, live companies only

    def company(name):
        return companies.get(name.casefold()) if name else None

    list_by_name = {}
    for row in ip_list:
        role = ip_role(row)
        row['role'] = role
        list_by_name.setdefault(row['name'].casefold(), row)
        if role in ('ip', 'not_ip') and _real_company(row['company']):  # a partner row's company is not its home
            companies.setdefault(row['company'].casefold(), ('company', row['company'].casefold()))
    for code in ('pop_mart',):  # the website's own catch-all maker is always a company
        companies.setdefault(MAKERS[code].casefold(), ('company', MAKERS[code].casefold()))
    names = {row['company'].casefold(): row['company'] for row in ip_list if _real_company(row['company'])}
    for folded, key in companies.items():
        _add(p, key, kind='company', name=names.get(folded, MAKERS.get('pop_mart')), parent=None)

    ips = {entry.get('website_id'): entry for entry in by_kind['ip']}
    export_ip_by_name = {}
    for entry in by_kind['ip']:
        export_ip_by_name.setdefault((entry.get('name') or '').strip().casefold(), entry)
    for row in ip_list:
        if row['name'].casefold() not in export_ip_by_name:
            p.unmatched.append({'side': 'IP list', 'name': row['cell'], 'detail':
                                f'{row["company"]} · {row["what"] or "-"} · decision: {row["decision"] or "(blank: keep)"}'
                                + (' · made as a new IP (New to the Dex)' if row['role'] == 'ip' and company(row['company'])
                                   else '')})
    for entry in by_kind['ip']:
        if (entry.get('name') or '').strip().casefold() not in list_by_name:
            p.unmatched.append({'side': 'Website', 'name': entry.get('name'), 'detail': f'website IP number {entry.get("website_id")}'})

    partners = {}  # casefold partner name -> key
    for row in ip_list:
        if row['role'] != 'partner':
            continue
        name = _rename(row['decision']) or row['proper']
        key = ('partner', name.casefold())
        partners[row['name'].casefold()] = key
        match = export_ip_by_name.get(row['name'].casefold())
        if key in p.rows:
            if match and p.rows[key]['website_id'] is None:
                _set_source(p.rows[key], match)
            continue
        _add(p, key, kind='partner', name=name, parent=None)
        if match:
            _set_source(p.rows[key], match)

    # Series makers, to place series and rows the list gives no company.
    series_by_ip = defaultdict(list)
    for entry in by_kind['series']:
        series_by_ip[entry.get('parent_website_id')].append(entry)

    def maker(entry):
        return company(MAKERS.get((entry.get('manufacturer') or '').strip()))

    def ip_company(row, ip_entry):
        found = company(row['company']) if row and _real_company(row['company']) else None
        if found:
            return found
        votes = Counter(maker(s) for s in series_by_ip.get(ip_entry.get('website_id'), []) if maker(s))
        if votes:
            return votes.most_common(1)[0][0]
        return company(row['was_under']) if row else None

    # IPs ------------------------------------------------------------------------------------------------
    ip_fate = {}  # website IP number -> ('ip'|'drop'|'not_ip'|'partner'|'unknown', key or None, list row)
    for entry in by_kind['ip']:
        wid = entry.get('website_id')
        row = list_by_name.get((entry.get('name') or '').strip().casefold())
        role = row['role'] if row else 'unknown'
        if role == 'partner':
            ip_fate[wid] = ('partner', partners[row['name'].casefold()], row)
            continue
        home = ip_company(row, entry)
        key = ('ip', wid)
        if home is None and role == 'drop':  # its company keeps no IP: a deleted row holds it
            name = (row['company'] if _real_company(row['company']) else row['was_under']) or 'Unknown company'
            home = ('company', name.casefold())
            if home not in p.rows:
                _add(p, home, kind='company', name=name, parent=None, deleted=True)
        if home is None:
            ip_fate[wid] = ('unknown', None, row)
            p.flag('IP', None, entry.get('name'), 'No company found for this IP (not in the IP list, no maker on its series). '
                   'Not loaded.', website_id=wid)
            continue
        name = (_rename(row['decision']) if row and role == 'ip' else None) or entry.get('name')
        _add(p, key, kind='ip', name=name, parent=home, deleted=role in ('drop', 'not_ip'), entry=entry)
        ip_fate[wid] = (role if role in ('drop', 'not_ip') else 'ip', key, row)
        if role == 'unknown':
            ip_fate[wid] = ('ip', key, row)
            p.flag('IP', key, entry.get('name'),
                   'Not in the IP list; loaded under the company most of its series name.', website_id=wid)
        if role in ('drop', 'not_ip'):
            p.dropped.append({'key': key, 'name': entry.get('name'), 'why': 'The owner marked it delete' if role == 'drop'
                              else 'Not an IP: its series move to the company', 'decision': row['decision'] if row else ''})

    # Own IPs the owner's list keeps that the website has no row for: the list is the owner's word that they
    # exist, so they are made, with no website number ("New to the Dex").
    for row in ip_list:
        if row['role'] != 'ip' or row['name'].casefold() in export_ip_by_name or not company(row['company']):
            continue
        name = _rename(row['decision']) or row['proper']
        key = ('ip', 'new:' + name.casefold())
        if key not in p.rows:
            _add(p, key, kind='ip', name=name, parent=company(row['company']))
            p.rows[key]['new_to_dex'] = True

    own_ip_keys = [fate[1] for fate in ip_fate.values() if fate[0] == 'ip'] + \
        [key for key, row in p.rows.items() if row.get('new_to_dex')]
    own_ip_names = {' '.join(re.sub(r'[^\w]+', ' ', p.rows[key]['name'].casefold()).split()): key
                    for key in own_ip_keys}

    def ip_of(entry):
        """The fate of the IP row an entry names as its parent (parent_kind 'ip', contract 1.2)."""
        if entry.get('parent_kind') not in (None, 'ip'):
            return None
        return ip_fate.get(entry.get('parent_website_id'))

    # Characters -----------------------------------------------------------------------------------------
    for entry in by_kind['character']:  # under its IP, or under the partner its IP row became (contract 1.2)
        fate = ip_of(entry)
        if fate and fate[0] in ('ip', 'drop', 'partner'):
            _add(p, ('character', entry.get('website_id')), kind='character', name=entry.get('name'), parent=fate[1],
                 deleted=fate[0] == 'drop', entry=entry)
        else:
            why = 'its IP is "not an IP"' if fate and fate[0] == 'not_ip' else 'it has no IP'
            p.flag('Character', None, entry.get('name'), f'Not loaded: {why}, and a character sits under an IP or a '
                   'partner. Pick where it goes, or leave it out.', website_id=entry.get('website_id'))
    characters = {key[1] for key in p.rows if key[0] == 'character'}
    character_ip = {key[1]: p.rows[key]['parent'] for key in p.rows if key[0] == 'character'}

    def char_key(entry):
        cid = entry.get('character_website_id')
        return ('character', cid) if cid in characters else None

    # Series ---------------------------------------------------------------------------------------------
    for entry in by_kind['series']:
        wid = entry.get('website_id')
        fate = ip_of(entry)
        key, partner, deleted, parent = ('series', wid), None, False, None
        list_row = fate[2] if fate else None
        collab = ip_fate.get(entry.get('collab_website_id'))
        if collab and collab[0] == 'partner':
            partner = collab[1]
        if fate is None or fate[0] == 'unknown':
            p.flag('Series', None, entry.get('name'), 'Not loaded: its IP is not loaded.', website_id=wid)
            continue
        if fate[0] in ('ip', 'drop'):
            parent, deleted = fate[1], fate[0] == 'drop'
        else:
            from_ip = fate[1]  # under a partner row or a "not an IP" row: a home IP if plain, else the company
            if fate[0] == 'partner':
                partner = fate[1]
                home = _home_ip(entry, own_ip_names, character_ip, characters)
                if home:
                    parent = home
            if parent is None:
                parent = maker(entry)
                blank = not (entry.get('manufacturer') or '').strip()
                if parent is None and list_row:
                    parent = company(list_row['company']) or company(list_row['was_under'])
                if parent is None:
                    p.flag('Series', None, entry.get('name'), 'Not loaded: no company (blank maker).', website_id=wid)
                    continue
                if fate[0] == 'partner':
                    p.flag('Series', key, entry.get('name'), 'A partner series with no clear home IP: put under the company '
                           f'its maker names, partner {p.rows[fate[1]]["name"]}. Pick its home IP if it has one.',
                           website_id=wid, choices=_key_note(p, parent))
                if blank:
                    p.flag('Series', key, entry.get('name'), 'Blank maker on the website: company taken from the IP list.',
                           website_id=wid, choices=_key_note(p, parent))
        ck = char_key(entry)
        _add(p, key, kind='series', name=entry.get('name'), parent=parent, deleted=deleted, entry=entry,
             partner=partner, character=ck)
        if fate[0] in ('partner', 'not_ip'):
            p.rows[key]['from_ip'] = from_ip

    # Products: product lines (under a series) and items (under an IP). Contract 1.2: `item` says which, and line
    # and item numbers overlap, so an item's key is ('item', number).
    for entry in by_kind['product']:
        wid = entry.get('website_id')
        item = entry.get('item')
        if item is None:
            item = {'series': False, 'ip': True}.get(entry.get('parent_kind'))
        if item is None:
            p.flag('Product', None, entry.get('name'), 'Not loaded: the export does not say whether it is an item or a '
                   'product line.', website_id=wid)
            continue
        key = ('item', wid) if item else ('product', wid)
        if wid is None:  # known to exist, number not known: found by its parent and name
            key = (key[0], f'new:{entry.get("parent_kind")}:{entry.get("parent_website_id")}:{norm(entry.get("name"))}')
        if key in p.rows:
            p.flag('Product', None, entry.get('name'), f'Not loaded: the export has website number {wid} twice.',
                   website_id=wid)
            continue
        if not item:
            parent = ('series', entry.get('parent_website_id'))
            if parent not in p.rows:
                p.flag('Product', None, entry.get('name'), 'Not loaded: its series is not loaded.', website_id=wid)
                continue
        else:
            fate = ip_of(entry)
            if not fate or fate[0] in ('unknown', 'partner'):
                p.flag('Product', None, entry.get('name'), 'Not loaded: an item with no IP it can sit under.', website_id=wid)
                continue
            parent = fate[1] if fate[0] in ('ip', 'drop') else company(fate[2]['company']) if fate[2] else None
            if parent is None:
                p.flag('Product', None, entry.get('name'), 'Not loaded: an item with no IP it can sit under.', website_id=wid)
                continue
        extras = dict(entry.get('extras') or {})
        if entry.get('parent_category') and 'parent_category' not in extras:
            extras['parent_category'] = entry['parent_category']
        _add(p, key, kind='product', name=entry.get('name'), parent=parent, entry=entry, extras=extras,
             deleted=p.rows[parent]['deleted'], character=char_key(entry),
             blind_box=None if entry.get('blind_box') is None else bool(entry.get('blind_box')))
        p.rows[key]['website_item'] = int(bool(item))

    for entry in by_kind['design']:
        wid = entry.get('website_id')
        parent = ('product', entry.get('parent_website_id'))  # a design's parent is always a product line
        if parent not in p.rows:
            p.flag('Design', None, entry.get('name'), 'Not loaded: its product is not loaded.', website_id=wid)
            continue
        key = ('design', wid) if wid is not None else \
            ('design', f'new:{entry.get("parent_website_id")}:{norm(entry.get("name"))}')
        if key in p.rows:
            p.flag('Design', None, entry.get('name'), 'Not loaded: the export has this design twice.', website_id=wid)
            continue
        rarity = entry.get('rarity')
        rarity = RARITY_WORDS.get(rarity, rarity) if rarity else None
        if rarity is not None and rarity not in dex_book.RARITIES:
            p.flag('Design', key, entry.get('name'), f'Unknown rarity "{rarity}"; loaded with none.', website_id=wid)
            rarity = None
        _add(p, key, kind='design', name=entry.get('name'), parent=parent, entry=entry, rarity=rarity,
             deleted=p.rows[parent]['deleted'], character=char_key(entry))

    for key, row in p.rows.items():  # everything under a stored-as-deleted row is stored as deleted
        if not row['deleted'] and _under_deleted(p, key):
            row['deleted'] = True

    _doubles(p)
    _against_database(con, p)
    _pair(con, p, evidence or [])
    _plan_suggestions(p)
    for flag in p.flags:
        key = flag.pop('key')
        full = flag.pop('full')
        flag['path'] = '' if key not in p.rows else path_of(p, key) + (' › ' + p.rows[key]['name'] if full else '')
    return p


def _add(p, key, kind, name, parent, deleted=False, entry=None, extras=None, partner=None, character=None,
         blind_box=None, rarity=None):
    entry = entry or {}
    p.rows[key] = {
        'key': key, 'kind': kind, 'name': ' '.join(str(name or '').split()), 'parent': parent, 'partner': partner,
        'character': character, 'deleted': deleted, 'website_id': entry.get('website_id') if entry else None,
        'sort_order': entry.get('sort_order') or 0, 'blind_box': blind_box, 'rarity': rarity,
        'picture_id': entry.get('picture_id') or None, 'picture_url': entry.get('picture_url') or None,
        'thumb_url': entry.get('thumb_url') or None,
        'extras': _clean_extras(kind, extras if extras is not None else entry.get('extras')),
        'listing_ids': [str(i).strip().upper() for i in entry.get('listing_ids') or [] if str(i).strip()],
        'product_id': None, 'paired': None, 'action': None, 'id': None, 'existing': None, 'website_item': 0,
    }


def _set_source(row, entry):
    """A partner keeps the website IP row's number and picture."""
    row['website_id'] = entry.get('website_id')
    row['picture_id'] = entry.get('picture_id') or None
    row['picture_url'] = entry.get('picture_url') or None
    row['thumb_url'] = entry.get('thumb_url') or None


def _key_note(p, key):
    return f'Now under: {p.rows[key]["name"]}' if key in p.rows else ''


def _home_ip(entry, own_ip_names, character_ip, characters):
    """A partner series' home IP when it is plain: its character is an own IP's character, or an own IP's name
    is a whole word of the series name."""
    cid = entry.get('character_website_id')
    if cid in characters and character_ip.get(cid) in own_ip_names.values():
        return character_ip[cid]
    words = ' '.join(re.sub(r'[^\w]+', ' ', (entry.get('character_name') or '').casefold()).split())
    if words and words in own_ip_names:
        return own_ip_names[words]
    name = ' ' + ' '.join(re.sub(r'[^\w]+', ' ', unicodedata.normalize('NFKC', entry.get('name') or '').casefold()).split()) + ' '
    found = [key for folded, key in own_ip_names.items() if folded and f' {folded} ' in name]
    return found[0] if len(found) == 1 else None


def _under_deleted(p, key):
    seen = set()
    parent = p.rows[key]['parent']
    while parent and parent in p.rows and parent not in seen:
        seen.add(parent)
        if p.rows[parent]['deleted']:
            return True
        parent = p.rows[parent]['parent']
    return False


def _subtree_keys(p, key):
    under = defaultdict(list)
    for k, row in p.rows.items():
        if row['parent']:
            under[row['parent']].append(k)
    out, stack = [], [key]
    while stack:
        k = stack.pop()
        out.append(k)
        stack.extend(under.get(k, []))
    return out


def _doubles(p):
    """Two entries of one kind under one parent with the same name (the website has doubles): the one with the
    most under it loads; the others are left out and listed for the owner."""
    groups = defaultdict(list)
    for key, row in p.rows.items():
        groups[(row['kind'], row['parent'], row['name'].casefold())].append(key)
    sizes = {}
    for keys in groups.values():
        if len(keys) < 2:
            continue
        for key in keys:
            sizes[key] = len(_subtree_keys(p, key))
        keys.sort(key=lambda k: (-sizes[k], k[1] if isinstance(k[1], int) else 0))
        for extra in keys[1:]:
            gone = _subtree_keys(p, extra)
            row = p.rows[extra]
            p.flag(row['kind'].capitalize(), row['parent'], row['name'],
                   f'Not loaded: same name as website number {keys[0][1]} under the same parent (a double on the website). '
                   f'{len(gone) - 1} entries under it are left out too.', website_id=row['website_id'],
                   choices=f'Kept: website number {keys[0][1]}', full=True)
            for k in gone:
                p.rows.pop(k, None)


def path_of(p, key):
    names, seen = [], set()
    parent = p.rows[key]['parent'] if key in p.rows else None
    while parent and parent in p.rows and parent not in seen:
        seen.add(parent)
        names.insert(0, p.rows[parent]['name'])
        parent = p.rows[parent]['parent']
    return ' › '.join(names)


_COMPARE = ('name', 'parent_id', 'partner_id', 'character_id', 'sort_order', 'blind_box', 'rarity', 'picture_id',
            'picture_url', 'thumb_url', 'extras', 'deleted')


def _against_database(con, p):
    """For each planned row: add, update (a row the load wrote), same, person (a person changed it: left alone),
    adopt (a hand-made entry with the same kind, parent and name)."""
    existing = [dict(row) for row in con.execute('SELECT * FROM dex_entries')]
    by_website = {(row['kind'], row['website_item'], row['website_id']): row for row in existing
                  if row['website_id'] is not None}
    top = {(row['kind'], row['name'].casefold()): row for row in existing
           if row['kind'] in ('company', 'partner') and row['parent_id'] is None}
    loose = defaultdict(list)  # hand-made: no website number, live
    for row in existing:
        if row['website_id'] is None and row['deleted_at'] is None:
            loose[(row['kind'], row['parent_id'], row['name'].casefold())].append(row)
    ids = {}
    for kind in ORDER:
        for key, row in p.rows.items():
            if row['kind'] != kind:
                continue
            parent_id = ids.get(row['parent']) if row['parent'] else None
            found = None
            if row['website_id'] is not None:
                found = by_website.get((kind, row['website_item'], row['website_id']))
            if found is None and kind in ('company', 'partner'):
                found = top.get((kind, row['name'].casefold()))
            adopt = False
            if found is None and (row['parent'] is None or parent_id is not None):
                for candidate in loose.get((kind, parent_id, row['name'].casefold()), []):
                    # the load's own row with no website number (an IP new to the Dex) is not adopted again
                    found, adopt = candidate, candidate['updated_by'] != LOAD
                    loose[(kind, parent_id, row['name'].casefold())].remove(candidate)
                    break
            if found is None:
                row['action'] = 'add'
                continue
            ids[key] = row['id'] = found['id']
            row['existing'] = found
            if adopt:
                row['action'] = 'adopt'
            elif found['updated_by'] != LOAD:
                row['action'] = 'person' if _differs(row, found, ids) else 'same'
            else:
                row['action'] = 'update' if _differs(row, found, ids) else 'same'
    p.ids = ids
    planned_ids = {row['id'] for row in p.rows.values() if row['id']}
    p.gone = [row for row in existing if row['updated_by'] == LOAD and row['website_id'] is not None
              and row['id'] not in planned_ids and row['deleted_at'] is None]


def _values(row, ids):
    return {'name': row['name'], 'parent_id': ids.get(row['parent']) if row['parent'] else None,
            'partner_id': ids.get(row['partner']) if row['partner'] else None,
            'character_id': ids.get(row['character']) if row['character'] else None,
            'sort_order': row['sort_order'], 'blind_box': None if row['blind_box'] is None else int(row['blind_box']),
            'rarity': row['rarity'], 'picture_id': row['picture_id'], 'picture_url': row['picture_url'],
            'thumb_url': row['thumb_url'], 'extras': row['extras'], 'deleted': row['deleted']}


def _stored(found):
    try:
        extras = json.loads(found['extras'] or '{}')
    except ValueError:
        extras = {}
    return {'name': found['name'], 'parent_id': found['parent_id'], 'partner_id': found['partner_id'],
            'character_id': found['character_id'], 'sort_order': found['sort_order'], 'blind_box': found['blind_box'],
            'rarity': found['rarity'], 'picture_id': found['picture_id'], 'picture_url': found['picture_url'],
            'thumb_url': found['thumb_url'], 'extras': extras, 'deleted': found['deleted_at'] is not None}


def _differs(row, found, ids):
    want, have = _values(row, ids), _stored(found)
    if any(row[field] and field_key not in ids for field, field_key in
           (('parent', row['parent']), ('partner', row['partner']), ('character', row['character']))):
        return True
    return any(want[field] != have[field] for field in _COMPARE)


# Pairing to POPCORE items --------------------------------------------------------------------------------

def _items(con):
    rows = [dict(row) for row in con.execute(
        """SELECT p.id, p.sku, p.name_cn_en, p.jizhanming, p.design_name, p.stock_form, p.series_id, p.ip_series,
                  p.brand, ps.product_id AS family_owner
           FROM products p LEFT JOIN product_series ps ON ps.id=p.series_id""")]
    return rows


def _box_sku(sku):
    sku = sku.upper()
    found = re.match(r'^(.+?)-(SET|\d{2,3})$', sku)
    return found.group(1) if found else sku


def _pair(con, p, evidence=()):
    items = _items(con)
    by_sku = {row['sku'].upper(): row for row in items if row['sku']}
    taken = {}  # product id -> entry id, for live links that stay
    for row in con.execute('SELECT id, product_id, updated_by FROM dex_entries WHERE product_id IS NOT NULL AND deleted_at IS NULL'):
        taken[row['product_id']] = row['id']
    item_by_id = {item['id']: item for item in items}
    used = set()

    def keep_existing(row):
        """A row that already has a link keeps it (a person's link is never replaced)."""
        found = row['existing']
        if found and found['product_id']:
            row['product_id'] = found['product_id']
            used.add(found['product_id'])
            item = item_by_id.get(found['product_id'])
            if item:
                p.paired.append({'sku': item['sku'], 'item': item['name_cn_en'] or item['jizhanming'], 'key': row['key'],
                                 'how': 'Already linked (kept)' if found['updated_by'] == LOAD
                                 else 'Already linked by a person (kept)'})
            return True
        return False

    def free(item, row):
        if item['id'] in used:
            return False
        holder = taken.get(item['id'])
        return holder is None or holder == row['id']

    def link(row, item, how):
        row['product_id'] = item['id']
        row['paired'] = how
        used.add(item['id'])
        p.paired.append({'sku': item['sku'], 'item': item['name_cn_en'] or item['jizhanming'], 'key': row['key'], 'how': how})

    live = [row for row in p.rows.values() if not row['deleted'] and row['kind'] in ('product', 'design')
            and row['action'] != 'person']
    for row in live:
        keep_existing(row)
    for row in p.rows.values():  # rows a person changed keep whatever link they have
        if row['action'] == 'person':
            keep_existing(row)

    designs = [row for row in live if row['kind'] == 'design' and not row['product_id']]
    products = [row for row in live if row['kind'] == 'product' and not row['product_id']]

    # 1. Designs by their own IDs.
    for row in designs:
        hits = {by_sku[i]['id']: by_sku[i] for i in row['listing_ids']
                if i in by_sku and by_sku[i]['stock_form'] == 'confirmed_design'}
        hits = [item for item in hits.values() if free(item, row)]
        if len(hits) == 1:
            link(row, hits[0], 'By ID (website listing)')
        elif len(hits) > 1:
            p.flag('Design', row['key'], row['name'], 'Several POPCORE designs carry this design\'s listings.',
                   ', '.join(item['sku'] for item in hits), row['website_id'])
    # 2. Products (items) by their own IDs.
    for row in products:
        hits = {}
        for i in row['listing_ids']:
            item = by_sku.get(i) or by_sku.get(_box_sku(i))
            if item and item['stock_form'] not in ('confirmed_design', 'sealed_set') and free(item, row):
                hits[item['id']] = item
        if len(hits) == 1:
            link(row, next(iter(hits.values())), 'By ID (website listing)')
    # Earlier linking work (--evidence), after the ID pairing, so that what it pairs counts for the box and series
    # steps below as it will on a re-run.
    _pair_by_evidence(p, evidence, by_sku, used, taken, free, link)
    products = [row for row in products if not row['product_id']]
    # 3. A product line through its designs' IDs, then through its series.
    children = defaultdict(list)
    for row in p.rows.values():
        if row['parent']:
            children[row['parent']].append(row)
    for row in products:
        if row['product_id']:
            continue
        boxes = {_box_sku(item_by_id[child['product_id']]['sku']) for child in children[row['key']]
                 if child['kind'] == 'design' and child['product_id'] in item_by_id}
        boxes = [by_sku[sku] for sku in boxes if sku in by_sku
                 and by_sku[sku]['stock_form'] not in ('confirmed_design', 'sealed_set')]
        if len(boxes) == 1 and free(boxes[0], row):
            link(row, boxes[0], "By ID (its designs' listings)")
    by_series = defaultdict(list)
    for row in products:
        if row['parent'] and row['parent'][0] == 'series':
            by_series[row['parent']].append(row)
    for series_key, rows in by_series.items():
        series = p.rows[series_key]
        candidates = {}
        for i in series['listing_ids']:
            item = by_sku.get(_box_sku(i))
            if item and item['stock_form'] not in ('confirmed_design', 'sealed_set') and item['id'] not in used:
                candidates[item['id']] = item
        open_rows = [row for row in rows if not row['product_id']]
        if not candidates or not open_rows:
            continue
        if len(rows) == 1 and len(candidates) == 1:
            item = next(iter(candidates.values()))
            if free(item, open_rows[0]):
                link(open_rows[0], item, 'Through its series (one product, one box)')
            continue
        for item in list(candidates.values()):
            label = item['name_cn_en'] or item['jizhanming'] or ''
            exact = [row for row in open_rows if not row['product_id'] and
                     norm(label) in (norm(row['name']), norm(series['name'] + ' ' + row['name']))]
            if len(exact) == 1 and free(item, exact[0]):
                link(exact[0], item, 'By name in its series')
                continue
            scored = sorted(((max(fuzz.ratio(norm(label), norm(row['name'])),
                                  fuzz.ratio(norm(label), norm(series['name'] + row['name']))), row)
                             for row in open_rows if not row['product_id']), key=lambda pair: -pair[0])
            if scored and scored[0][0] >= CLOSE:
                p.flag('Product', scored[0][1]['key'], scored[0][1]['name'],
                       f'Name close to {item["sku"]} “{label}” but not equal. Pair them?', item['sku'],
                       scored[0][1]['website_id'])
            else:
                why = (f'POPCORE item {item["sku"]} is listed with this series. The series has one product and '
                       'several POPCORE items are listed with it, so the item is not paired. Pick the product, or none.'
                       if len(rows) == 1 else
                       f'POPCORE item {item["sku"]} is listed with this series, which has {len(rows)} products and no '
                       'clear name match. Pick the product.')
                p.flag('Box', series_key, label, why,
                       ', '.join(f'{row["name"]} (website {row["website_id"]})' for row in open_rows), None, full=True)
    # 4. Designs by name inside an already paired box.
    family = defaultdict(list)
    for item in items:
        if item['stock_form'] == 'confirmed_design' and item['family_owner']:
            family[item['family_owner']].append(item)
    for row in [row for row in live if row['kind'] == 'design' and not row['product_id']]:
        box = p.rows.get(row['parent'], {}).get('product_id')
        if not box:
            continue
        options = [item for item in family.get(box, []) if free(item, row)]
        exact = [item for item in options if norm(item['design_name']) == norm(row['name'])]
        if len(exact) == 1:
            link(row, exact[0], 'By name in its box')
            continue
        scored = sorted(((fuzz.ratio(norm(item['design_name']), norm(row['name'])), item) for item in options),
                        key=lambda pair: -pair[0])
        if scored and scored[0][0] >= CLOSE:
            p.flag('Design', row['key'], row['name'],
                   f'Name close to {scored[0][1]["sku"]} “{scored[0][1]["design_name"]}” but not equal. Pair them?',
                   scored[0][1]['sku'], row['website_id'])
    # A claim asked about that a later step paired the same way needs no pick.
    settled = [flag for flag, item_id, key in getattr(p, 'evidence_asked', [])
               if key in p.rows and item_id and p.rows[key]['product_id'] == item_id]
    if settled:
        p.flags = [flag for flag in p.flags if not any(flag is done for done in settled)]
        p.evidence['your_pick'] -= len(settled)
        p.evidence['agrees'] += len(settled)
    # Not paired: POPCORE items with no live book link after the load.
    linked = set(used) | set(taken)
    p.linked_items = linked
    p.not_paired = [item for item in items if item['stock_form'] in LINKABLE and item['id'] not in linked]
    for row in p.rows.values():  # a pairing on a row the load leaves alone is not made
        if row['paired'] and row['action'] in ('person', 'same') and row['existing'] and \
                row['existing']['product_id'] != row['product_id']:
            row['action'] = 'update' if row['action'] == 'same' else row['action']


def _pair_by_evidence(p, evidence, by_sku, used, taken, free, link):
    """A high claim pairs the item ("Earlier linking work"). A medium claim, one that conflicts with the listing IDs,
    with the pairing already made or with another claim, or one that names an entry the export lacks goes to the
    owner's pick with the choice filled in. A link a person made is never touched."""
    p.evidence = Counter()
    p.evidence_asked = []  # (flag, item id, entry key): dropped at the end when a later step pairs them the same way
    if not evidence:
        return
    holder_of = {row['product_id']: row for row in p.rows.values() if row['product_id']}  # item id -> its entry
    by_entry = {}
    for key, row in p.rows.items():
        if row['kind'] in ('product', 'design') and row['website_id'] is not None and not row['deleted']:
            by_entry[('item' if row['website_item'] else row['kind'], row['website_id'])] = key
    claims = defaultdict(list)
    for claim in evidence:
        claims[claim['sku']].append(claim)
    claimed = defaultdict(set)  # entry key -> POPCORE IDs claiming it
    for sku, rows in claims.items():
        for claim in rows:
            key = by_entry.get(('item' if claim['item'] else claim['kind'], claim['website_id']))
            claim['key'] = key
            if key:
                claimed[key].add(sku)

    def where(claim):
        kind = 'item' if claim['item'] else claim['kind']
        return f'{kind} {claim["website_id"] if claim["website_id"] is not None else "(no number)"}' + \
            (f' in {claim["series_slug"]}' if claim['series_slug'] else '')

    def ask(claim, item, why):
        p.evidence['your_pick'] += 1
        label = (item or {}).get('name_cn_en') or (item or {}).get('jizhanming') or ''
        key = claim['key']
        row = p.rows.get(key) if key else None
        name = row['name'] if row else label or claim['sku']
        p.flag('Earlier linking', key, name, f'{why} Source: {claim["source"]} ({claim["date"]}, {claim["confidence"]})'
               + (f'. {claim["note"]}' if claim['note'] else '') + '.',
               f'Pair {claim["sku"]} “{label}” with {where(claim)}', row['website_id'] if row else claim['website_id'],
               full=True)
        p.evidence_asked.append((p.flags[-1], (item or {}).get('id'), key))

    for sku in sorted(claims):
        rows = claims[sku]
        item = by_sku.get(sku)
        targets = {claim['key'] for claim in rows}
        newest = sorted(rows, key=lambda c: c['date'], reverse=True)
        best = sorted(newest, key=lambda c: c['confidence'] != 'high')[0]  # high first, then the newest
        if item is None:
            p.evidence['popcore_id_unknown'] += 1
            continue
        if None in targets:
            ask(next(c for c in rows if c['key'] is None), item, 'The claim names a book entry the export lacks (or one stored as deleted).')
            continue
        if len(targets) > 1:
            ask(best, item, 'Sources disagree about this POPCORE item: ' + '; '.join(
                f'{where(c)} ({c["source"]})' for c in rows) + '.')
            continue
        key = best['key']
        row = p.rows[key]
        if (item['stock_form'] == 'confirmed_design') != (row['kind'] == 'design') or item['stock_form'] == 'sealed_set':
            ask(best, item, 'The POPCORE item is not of the kind the entry is (a design pairs with a design, a product '
                'with a box or an item).')
            continue
        if row['product_id'] == item['id']:
            p.evidence['agrees'] += 1
            continue
        if item['id'] in used or item['id'] in taken:
            holder = holder_of.get(item['id'])
            ask(best, item, 'The item is already paired with another entry'
                + (f' ({holder["name"]}, {holder["paired"] or "kept"}).' if holder else '.'))
            continue
        if row['action'] == 'person' or row['product_id']:
            by_person = row['action'] == 'person' or (row['existing'] or {}).get('updated_by', LOAD) != LOAD
            ask(best, item, 'The entry is already paired with another item' + (' by a person' if by_person else '') + '.')
            continue
        if len(claimed[key]) > 1:
            ask(best, item, 'Several POPCORE items are claimed for this entry: ' + ', '.join(sorted(claimed[key])) + '.')
            continue
        others = [i for i in row['listing_ids'] if i != sku and (row['kind'] == 'design' or _box_sku(i) != sku)
                  and i in by_sku and (by_sku[i]['stock_form'] == 'confirmed_design') == (row['kind'] == 'design')]
        if others:
            ask(best, item, 'The website listings tie this entry to ' + ', '.join(sorted(set(others))) + '.')
            continue
        if best['confidence'] != 'high':
            ask(best, item, 'A medium claim.')
            continue
        if not free(item, row):
            ask(best, item, 'The item is already linked elsewhere.')
            continue
        link(row, item, f'Earlier linking work ({best["source"]})')
        p.evidence['paired'] += 1


def suggest_ip(item, ip_names):
    group = (item.get('ip_series') or '').strip()
    if not group:
        return ''
    folded = group.casefold()
    if folded == 'smiski & sa':
        brand = (item.get('brand') or '').casefold()
        return 'Smiski' if 'smiski' in brand or 'smiski' in (item.get('name_cn_en') or '').casefold() else 'Sonny Angel'
    if folded in GROUP_WORDS:
        return GROUP_WORDS[folded]
    return ip_names.get(folded, '')


# Suggestions for items that stay unpaired ---------------------------------------------------------------
# Never applied by the load: a suggestion is shown to a person, who links it or not.

FILLER = {'series', 'figure', 'figures', 'blind', 'box', 'boxes', 'vinyl', 'the', 'x', 'pop', 'mart', 'popmart', 'mini',
          'set', 'edition', 'toy', 'toys', 'collection', 'random', 'single', 'whole', 'a', 'of', 'and', 'in'}
SUGGEST_FLOOR = 60  # below this, no suggestion


def _words(text):
    text = unicodedata.normalize('NFKC', text or '').casefold().replace('×', ' x ')
    return re.sub(r'[^\w]+', ' ', text).split()


def _clean(text, drop):
    """A name without filler words, the IPs' own names and POPCORE IDs, for comparing."""
    return ' '.join(word for word in _words(text) if word not in FILLER and word not in drop
                    and not re.fullmatch(r'[a-z0-9]{2}\d{4}', word))


def _numbers(text):
    """The numbers in a name (a year, 400 against 1000, Series 2 against 3): two names are only alike when
    these agree, however close the words are."""
    return set(re.findall(r'\d+', text or ''))


def _has_word(text):
    """A real word is left to compare (not only "2" once the IP's name and the filler words are gone)."""
    return re.search(r'[^\W\d_]{3,}', text or '') is not None


def candidates(nodes):
    """Book products and designs no item is linked to, ready for suggest(). nodes: key -> {id, kind, name, parent,
    product_id} for the live book (the database's rows, or a load plan's)."""
    ip_words = set()
    for node in nodes.values():
        if node['kind'] == 'ip':
            ip_words.update(_words(node['name']))

    def chain(key):
        out, seen, parent = [], set(), nodes[key]['parent']
        while parent in nodes and parent not in seen:
            seen.add(parent)
            out.insert(0, nodes[parent])
            parent = nodes[parent]['parent']
        return out

    products, designs = [], defaultdict(list)
    for key, node in nodes.items():
        if node['product_id'] or node['kind'] not in ('product', 'design'):
            continue
        above = chain(key)
        path = ' › '.join([step['name'] for step in above] + [node['name']])
        if node['kind'] == 'design':
            parent = nodes.get(node['parent'])
            if parent and parent['product_id']:
                designs[parent['product_id']].append({'id': node['id'], 'path': path, 'name': node['name']})
            continue
        ip = next((step['name'].casefold() for step in reversed(above) if step['kind'] == 'ip'), '')
        series = above[-1]['name'] if above and above[-1]['kind'] == 'series' else ''
        products.append({'id': node['id'], 'path': path, 'ip': ip,
                         'texts': {_clean(series + ' ' + node['name'], ip_words), _clean(node['name'], ip_words)} - {''}})
    ip_names = {node['name'].casefold(): node['name'] for node in nodes.values() if node['kind'] == 'ip'}
    return {'products': products, 'designs': designs, 'ip_words': ip_words, 'ip_names': ip_names}


def suggest(item, found):
    """The best book entry for an unlinked POPCORE item by name, with a closeness score (0-100), or None.
    A box or item: among book products with no linked item, the IP its group or name points to first. A confirmed
    design: among the unlinked designs under the book product its box is linked to."""
    if item.get('stock_form') == 'confirmed_design':
        box = item.get('family_owner') or (item.get('box') or {}).get('id')
        name = norm(item.get('design_name') or '')
        scored = [(fuzz.ratio(name, norm(c['name'])), c) for c in found['designs'].get(box, [])
                  if _numbers(c['name']) == _numbers(name)] if name else []
    else:
        hint = suggest_ip(item, found['ip_names']).casefold()
        label = item.get('name_cn_en') or item.get('jizhanming') or ''
        if not hint:
            spaced = ' ' + ' '.join(_words(label)) + ' '
            hint = next((ip for ip in found['ip_names'] if f' {" ".join(_words(ip))} ' in spaced), '')
        # A known IP narrows the search to that IP's products, and stays narrow when it has none: the whole
        # book is searched only when the item points at no IP at all.
        pool = [c for c in found['products'] if c['ip'] == hint] if hint else found['products']
        text = _clean(label, found['ip_words'])
        scored = []
        for c in pool if _has_word(text) else []:
            close = [fuzz.token_sort_ratio(text, t) for t in c['texts'] if _has_word(t) and _numbers(t) == _numbers(text)]
            if close:
                scored.append((max(close), c))
    if not scored:
        return None
    score, best = max(scored, key=lambda pair: pair[0])
    if score < SUGGEST_FLOOR:
        return None
    return {'id': best['id'], 'path': best['path'], 'score': int(round(score))}


def suggestions_from_db(con, items):
    """Add `suggestion` to each unlinked item, against the live book."""
    nodes = {row['id']: {'id': row['id'], 'kind': row['kind'], 'name': row['name'], 'parent': row['parent_id'],
                         'product_id': row['product_id']}
             for row in con.execute('SELECT id, kind, name, parent_id, product_id FROM dex_entries WHERE deleted_at IS NULL')}
    found = candidates(nodes)
    for item in items:
        item['suggestion'] = suggest(item, found)
    return items


def _plan_suggestions(p):
    nodes = {key: {'id': row['id'], 'kind': row['kind'], 'name': row['name'], 'parent': row['parent'],
                   'product_id': row['product_id']} for key, row in p.rows.items() if not row['deleted']}
    found = candidates(nodes)
    p.suggestions = {item['id']: suggest(item, found) for item in p.not_paired}


# Apply ---------------------------------------------------------------------------------------------------

def apply(con, p):
    """Write the plan in one transaction. Every row passes dex_book._check; on any refusal nothing is written."""
    if con.in_transaction:
        con.commit()
    con.execute('BEGIN IMMEDIATE')
    try:
        # Rows the load stored as deleted come back to life inside the transaction, so new rows under them pass
        # the parent rule; everything planned as deleted is marked again at the end.
        revived = {row['id']: row['deleted_at'] for row in con.execute(
            "SELECT id, deleted_at FROM dex_entries WHERE deleted_at IS NOT NULL AND updated_by=?", (LOAD,))}
        if revived:
            con.executemany('UPDATE dex_entries SET deleted_at=NULL WHERE id=?', [(i,) for i in revived])
        ids = dict(p.ids)
        counts = Counter()
        for kind in ORDER:
            for row in [row for row in p.rows.values() if row['kind'] == kind]:
                action = row['action']
                if action in ('same', 'person'):
                    if row['id'] is not None and row['existing'] and row['product_id'] != row['existing']['product_id'] \
                            and action == 'same':
                        action = 'update'
                    else:
                        counts[action] += 1
                        continue
                values = _values(row, ids)
                check = {'kind': kind, 'name': values['name'], 'parent_id': values['parent_id'],
                         'partner_id': values['partner_id'], 'character_id': values['character_id'],
                         'blind_box': values['blind_box'], 'rarity': values['rarity'], 'product_id': row['product_id'],
                         'extras': values['extras']}
                if action == 'adopt':  # keep what the person typed; fill what is blank
                    found = _stored(row['existing'])
                    check['name'] = found['name']
                    for field in ('partner_id', 'character_id', 'blind_box', 'rarity'):
                        if found[field] is not None:
                            check[field] = found[field]
                    check['extras'] = {**values['extras'], **found['extras']}
                    if row['existing']['product_id']:
                        check['product_id'] = row['existing']['product_id']
                try:
                    dex_book._check(con, check, row['id'])
                except DexError as exc:
                    raise DexError(f'{kind} “{row["name"]}” (website {row["website_id"]}): {exc}', exc.code, exc.status)
                extras = json.dumps(check['extras'], ensure_ascii=False)
                if action == 'add':
                    cur = con.execute(
                        """INSERT INTO dex_entries(kind, parent_id, partner_id, character_id, name, sort_order, blind_box,
                               rarity, product_id, picture_id, picture_url, thumb_url, extras, website_id, website_item,
                               updated_by)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (kind, check['parent_id'], check['partner_id'], check['character_id'], check['name'],
                         values['sort_order'], check['blind_box'], check['rarity'], check['product_id'],
                         values['picture_id'], values['picture_url'], values['thumb_url'], extras, row['website_id'],
                         row['website_item'], LOAD))
                    row['id'] = ids[row['key']] = cur.lastrowid
                else:
                    con.execute(
                        """UPDATE dex_entries SET kind=kind, parent_id=?, partner_id=?, character_id=?, name=?, sort_order=?,
                               blind_box=?, rarity=?, product_id=?, picture_id=COALESCE(?, picture_id),
                               picture_url=COALESCE(?, picture_url), thumb_url=COALESCE(?, thumb_url), extras=?,
                               website_id=?, website_item=?, version=version+1, updated_at=datetime('now'), updated_by=? WHERE id=?""",
                        (check['parent_id'], check['partner_id'], check['character_id'], check['name'],
                         values['sort_order'] if action == 'update' else row['existing']['sort_order'],
                         check['blind_box'], check['rarity'], check['product_id'], values['picture_id'],
                         values['picture_url'], values['thumb_url'], extras, row['website_id'], row['website_item'],
                         row['existing']['updated_by'] if action == 'adopt' else LOAD, row['id']))
                counts[action] += 1
        # Deleted rows, children first: kept with deleted_at so step 3 can tell the website.
        for kind in reversed(ORDER):
            for row in p.rows.values():
                if row['kind'] != kind or row['id'] is None:
                    continue
                if row['deleted']:
                    before = revived.get(row['id'])
                    if before:
                        con.execute('UPDATE dex_entries SET deleted_at=? WHERE id=?', (before, row['id']))
                    elif row['action'] != 'person':
                        con.execute("""UPDATE dex_entries SET deleted_at=datetime('now'), version=version+1,
                                       updated_at=datetime('now'), updated_by=? WHERE id=?""", (LOAD, row['id']))
                        counts['stored_deleted'] += 1
        planned_live = {row['id'] for row in p.rows.values() if row['id'] and not row['deleted']}
        for entry_id, before in revived.items():  # deleted rows the plan does not bring back stay deleted
            if entry_id not in planned_live:
                con.execute('UPDATE dex_entries SET deleted_at=? WHERE id=? AND deleted_at IS NULL', (before, entry_id))
        con.commit()
        return dict(counts)
    except Exception:
        con.rollback()
        raise


# Summary and review sheet --------------------------------------------------------------------------------

ACTION_WORDS = {'add': 'New', 'update': 'Updated', 'same': 'No change', 'person': 'Left alone: a person changed it',
                'adopt': 'Joined to an entry made by hand'}


def summary(p):
    live = Counter(row['kind'] for row in p.rows.values() if not row['deleted'])
    deleted = Counter(row['kind'] for row in p.rows.values() if row['deleted'])
    actions = Counter(row['action'] for row in p.rows.values())
    how = Counter('By ID' if pair['how'].startswith('By ID') else 'Through its series' if pair['how'].startswith('Through')
                  else 'Already linked, kept' if pair['how'].startswith('Already')
                  else 'Earlier linking work' if pair['how'].startswith('Earlier') else 'By name' for pair in p.paired)
    return {'entries_by_kind': {kind: live.get(kind, 0) for kind in ORDER},
            'stored_as_deleted_by_kind': {kind: deleted.get(kind, 0) for kind in ORDER if deleted.get(kind)},
            'actions': {ACTION_WORDS.get(k, k): v for k, v in actions.items()},
            'new_to_dex': sum(1 for row in p.rows.values() if row.get('new_to_dex')),
            'paired': dict(how), 'paired_total': len(p.paired), 'unsure': len(p.flags),
            'not_paired': len(p.not_paired),
            'not_paired_by_kind': dict(Counter(item['stock_form'] or 'unsorted' for item in p.not_paired)),
            'boxes_and_items_suggested_85_plus': sum(
                1 for item in p.not_paired if item['stock_form'] != 'confirmed_design'
                and (p.suggestions.get(item['id']) or {}).get('score', 0) >= 85), 'unmatched_names': len(p.unmatched),
            'left_alone_people': actions.get('person', 0), 'no_longer_in_export': len(p.gone),
            'no_website_number': sum(1 for row in p.rows.values() if row['website_id'] is None
                                     and row['kind'] not in ('company', 'partner') and not row.get('new_to_dex')),
            'evidence': {k: v for k, v in getattr(p, 'evidence', {}).items() if v}}


def sheet_tabs(con, p, ip_list):
    """The review sheet's tabs as (title, header, rows), in plain words."""
    s = summary(p)
    stock = {row[0]: row[1] for row in con.execute(
        'SELECT product_id, SUM(quantity) FROM inventory_balances GROUP BY product_id')}
    ip_names = {}
    for row in ip_list:
        if row.get('role', ip_role(row)) in ('ip', 'partner', 'not_ip'):
            ip_names[row['name'].casefold()] = row['proper'] if row.get('role', ip_role(row)) != 'ip' else row['name']
            ip_names[row['proper'].casefold()] = ip_names[row['name'].casefold()]
    kind_words = {'company': 'Company', 'partner': 'Partner', 'ip': 'IP', 'character': 'Character', 'series': 'Series',
                  'product': 'Product', 'design': 'Design'}
    form_words = {'random_box': 'Blind box', 'ordinary': 'Item', 'confirmed_design': 'Design', None: 'Not sorted yet'}

    rows = [('Book entries loaded', '')]
    rows += [(f'  {kind_words[kind]}', count) for kind, count in s['entries_by_kind'].items()]
    rows += [('  of which IPs new to the Dex (from the IP list)', s['new_to_dex'])]
    rows += [('Kept as deleted, for the website to delete', sum(s['stored_as_deleted_by_kind'].values()))]
    rows += [(f'  {kind_words[kind]}', count) for kind, count in s['stored_as_deleted_by_kind'].items()]
    rows += [('What the load does', '')] + [(f'  {word}', count) for word, count in s['actions'].items()]
    rows += [('POPCORE items paired', s['paired_total'])] + [(f'  {word}', count) for word, count in s['paired'].items()]
    rows += [('Your pick needed', s['unsure']), ('POPCORE items not in the book', s['not_paired'])]
    rows += [(f'  {form_words.get(None if form == "unsorted" else form, form)}', count)
             for form, count in s['not_paired_by_kind'].items()]
    rows += [('  Boxes and items with a suggestion 85 or closer', s['boxes_and_items_suggested_85_plus']),
             ('Names in only one of the IP list and the website', s['unmatched_names']),
             ('Entries the load made before that are no longer on the website (left as they are)', s['no_longer_in_export'])]
    rows += [('Note', note) for note in p.notes]
    tabs = [('Summary', ('What', 'How many'), rows)]

    loaded = sorted((row for row in p.rows.values()), key=lambda row: (ORDER.index(row['kind']), path_of(p, row['key']),
                                                                      row['name'].casefold()))
    tabs.append(('Loaded', ('Kind', 'Where it sits', 'Name', 'Website number', 'What the load does', 'Picture',
                            'POPCORE item'),
                 [(kind_words[row['kind']], path_of(p, row['key']), row['name'], row['website_id'] or '',
                   'Kept as deleted (the website will delete it)' if row['deleted'] else
                   'New to the Dex (from the IP list; the website has no row yet)'
                   if row.get('new_to_dex') and row['action'] == 'add' else
                   'New (no website number known)' if row['action'] == 'add' and row['website_id'] is None
                   and row['kind'] not in ('company', 'partner') else ACTION_WORDS.get(row['action'], ''),
                   'yes' if row['picture_url'] else '', _sku(con, row['product_id'])) for row in loaded]))
    tabs.append(('Paired', ('Item ID', 'Item name', 'Book entry', 'How it was paired'),
                 [(pair['sku'], pair['item'], f'{path_of(p, pair["key"])} › {p.rows[pair["key"]]["name"]}', pair['how'])
                  for pair in sorted(p.paired, key=lambda pair: pair['sku'])]))
    tabs.append(('Your pick', ('What', 'Where it sits', 'Name', 'Website number', 'Why it needs you', 'Choices',
                               'Your pick'),
                 [(flag['what'], flag['path'] or '', flag['name'], flag['website_id'] or '', flag['why'], flag['choices'], '')
                  for flag in p.flags]))
    skus = {item['id']: item['sku'] for item in p.not_paired}
    owners = {row[0]: row[1] for row in con.execute('SELECT id, sku FROM products')}

    def advice(item):
        box = item.get('family_owner')
        if item['stock_form'] == 'confirmed_design' and box and box not in p.linked_items:
            return f'Follows its box ({owners.get(box, skus.get(box, "?"))})', ''
        found = p.suggestions.get(item['id'])
        return (found['path'], found['score']) if found else ('', '')

    ordered = sorted(p.not_paired, key=lambda item: (item['stock_form'] == 'confirmed_design', item['sku'] or ''))
    tabs.append(('Not in the book', ('Item ID', '记账名', 'Name', 'Kind', 'POPCORE group', 'Brand', 'In stock',
                                     'Suggested IP', 'Suggested book entry', 'How close (of 100)'),
                 [(item['sku'], item['jizhanming'] or '', item['name_cn_en'] or '', form_words.get(item['stock_form'], ''),
                   item['ip_series'] or '', item['brand'] or '', stock.get(item['id'], 0), suggest_ip(item, ip_names),
                   *advice(item)) for item in ordered]))
    gone_rows = []
    for drop in p.dropped:
        under = Counter(p.rows[k]['kind'] for k in _subtree_keys(p, drop['key']) if k != drop['key'] and k in p.rows)
        moved = sum(1 for row in p.rows.values() if row['kind'] == 'series' and not row['deleted']
                    and drop['why'].startswith('Not an IP') and row['website_id'] is not None
                    and _was_under(row, drop))
        gone_rows.append((drop['name'], drop['why'], drop['decision'] or '', under.get('character', 0), under.get('series', 0),
                          under.get('product', 0), under.get('design', 0), moved or ''))
    tabs.append(('Deleted on the website', ('IP', 'Why', "Owner's note", 'Characters', 'Series', 'Products',
                                            'Designs', 'Series moved to the company'), gone_rows))
    tabs.append(('Names not matched', ('Found only in', 'Name', 'Details'),
                 [(row['side'], row['name'], row['detail']) for row in p.unmatched]))
    return tabs


def _was_under(row, drop):
    return row.get('from_ip') == drop['key']


def _sku(con, product_id):
    if not product_id:
        return ''
    found = con.execute('SELECT sku FROM products WHERE id=?', (product_id,)).fetchone()
    return found[0] if found else ''
