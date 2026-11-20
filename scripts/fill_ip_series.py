"""系列 IP fill: give an IP to the products the Dex gives none (owner, 2026-11-20: Dex is the IP source).

Scope: products whose 盘点 card has no Dex IP (no product on the card is linked to a Dex entry under an IP or partner)
and whose ip_series is blank, 其他系列 or POPMART. Such a card is grouped by its brand alone.

For each card in scope, in this order:
  1. The card's own 系列: another product on the card already has a 系列 naming a Dex IP or partner.
  2. The name (the card's head: 记账名 and product name; a series no product owns: the series' name) names Dex IPs,
     their characters, the store's shorthand (SHORTHAND) or the 系列 words in use. One IP is the answer; an IP whose
     company's code differs from the POPCORE ID's (BUNNY on a Finding Unicorn ID) does not count.
  3. With no IP named, the partners it names (Minions, Teletubbies …). One partner is the answer.
Two or more answers, or none, leave the card as it is and list it.

The 系列 written is the word already in use for that IP (Labubu, not THE MONSTERS; Smiski & SA for both Dreams IPs),
else the Dex name (capitals made Title Case). The CSV's dex column is the Dex IP the written word comes back to, the way
the section count reads a 系列 (dex_load.suggest_ip, then the Dex IP and partner names).

Dry run by default: reads only. --apply first takes an online backup to --backup (a new file), then plans again and
writes in ONE transaction. Re-running changes nothing.

    POPCORE_DB_PATH=/path/popcore.db python scripts/fill_ip_series.py [--out DIR] [--apply --backup /path/before.db]
"""
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))  # last: scripts/inventory_opening.py must not shadow the app's
import catalog_fix  # noqa: E402  (also puts popcore_app on the path)
from brands import canonical_brand  # noqa: E402
from blueprints.products import _BRAND_CODE_OF  # noqa: E402
from dex_load import GROUP_WORDS, suggest_ip  # noqa: E402
from normalize_brands import code_of  # noqa: E402

COLUMNS = ('action', 'why', 'check', 'product_id', 'sku', 'name', 'before', 'after', 'dex', 'matched', 'card')
EMPTY = ('', '其他系列', 'POPMART')
# Names the Dex does not hold, for the Dex IP or partner they mean: Labubu's family, the store's 记账名 shorthand and
# a few other names of a partner's world.
SHORTHAND = {'labubu': 'THE MONSTERS', '拉布布': 'THE MONSTERS', 'mokoko': 'THE MONSTERS', 'zimomo': 'THE MONSTERS',
             'sa': 'Sonny Angel', 'sp': 'SKULLPANDA', '星星人': 'TWINKLE TWINKLE', '哭娃': 'CRYBABY', '小野': 'HIRONO',
             '叛桃': 'PEACH RIOT', 'wkk': 'WAKUKU', '烤动物': 'Animal Bakery',
             'snoopy': 'Peanuts', 'kimetsu no yaiba': 'Demon Slayer', 'bikini bottom': 'SpongeBob SquarePants',
             '比奇堡': 'SpongeBob SquarePants'}
BOTH_DREAMS = 'Smiski & SA'  # the 系列 word for both Dreams IPs; suggest_ip tells them apart by brand and name
NO_IP_CODES = {'AC': 'accessory', 'GC': 'gift card'}


def _fold(text):
    text = unicodedata.normalize('NFKD', text or '').casefold().replace('×', ' x ').replace('&', ' and ')
    return ''.join(char for char in text if not unicodedata.combining(char))


def tokens(text):
    """Latin words and numbers apart (Ted2 → ted 2, sp餐桌 → sp 餐桌); a run of Chinese is one token."""
    text = re.sub(r'[^\w]+|_', ' ', _fold(text))
    text = re.sub(r'(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])|(?<=[a-z0-9])(?=[^\x00-\x7f])|(?<=[^\x00-\x7f])(?=[a-z0-9])',
                  ' ', text)
    return text.split()


def _named_in(name, words):
    """True when the name is in the text: as whole words (Skull Panda meets SKULLPANDA), or inside a Chinese run."""
    wanted = tokens(name)
    if not wanted:
        return False
    if all(re.fullmatch(r'[^\x00-\x7f]+', word) for word in wanted):
        return ''.join(wanted) in ''.join(word for word in words if not word.isascii())
    size = len(wanted)
    if any(words[at:at + size] == wanted for at in range(len(words) - size + 1)):
        return True
    squashed = ''.join(wanted)
    return len(squashed) >= 6 and any(''.join(words[at:at + span]) == squashed
                                      for span in (1, 2, 3) for at in range(len(words) - span + 1))


class Dex:
    """The Dex IPs and partners, the names that mean them and the 系列 word for each."""

    def __init__(self, con, catalog):
        rows = {row['id']: dict(row) for row in con.execute(
            "SELECT id, kind, name, parent_id FROM dex_entries WHERE deleted_at IS NULL "
            "AND kind IN ('company', 'ip', 'partner', 'character')")}
        self.targets = {}  # folded name -> (kind, name, company code or None)
        for row in sorted(rows.values(), key=lambda row: (row['kind'] != 'ip', row['id'])):
            if row['kind'] in ('ip', 'partner'):
                company = rows.get(row['parent_id']) if row['kind'] == 'ip' else None
                code = _BRAND_CODE_OF.get(canonical_brand(company['name']).lower()) if company else None
                self.targets.setdefault(row['name'].casefold(), (row['kind'], row['name'], code))
        self.names = {}  # name as written -> folded target name
        for row in rows.values():
            if row['kind'] in ('ip', 'partner'):
                self.names[row['name']] = row['name'].casefold()
            elif row['kind'] == 'character' and rows.get(row['parent_id'], {}).get('kind') in ('ip', 'partner'):
                self.names[row['name']] = rows[row['parent_id']]['name'].casefold()
        for name, target in (*SHORTHAND.items(), *GROUP_WORDS.items()):
            if target.casefold() in self.targets:
                self.names.setdefault(name, target.casefold())
        in_use = Counter()
        for product in catalog.products.values():
            word = (product['ip_series'] or '').strip()
            target = self.resolve(word, product) if word not in EMPTY else None
            if target:
                in_use[(target, word)] += 1
                if word != BOTH_DREAMS:
                    self.names.setdefault(word, target)
        self.word = {}  # folded target name -> the 系列 word to write
        for (target, word), _ in in_use.most_common():
            self.word.setdefault(target, BOTH_DREAMS if target in ('smiski', 'sonny angel') else word)

    def resolve(self, word, product):
        """The Dex IP or partner (folded name) a 系列 word means for this product, as the section count reads it."""
        if not (word or '').strip():
            return None
        named = suggest_ip({**product, 'ip_series': word}, {})
        folded = (named or word).strip().casefold()
        return folded if folded in self.targets else None

    def write_word(self, target):
        if target in self.word:
            return self.word[target]
        if target in ('smiski', 'sonny angel'):
            return BOTH_DREAMS
        name = self.targets[target][1]
        return name.title() if name.isupper() else name

    def found(self, text, code):
        """({target: [matched names]}, [names dropped because their IP's company is another code])."""
        words, hits, dropped = tokens(text), {}, []
        for name, target in self.names.items():
            if _named_in(name, words):
                company = self.targets[target][2]
                if company and code and company != code:
                    dropped.append(name)
                else:
                    hits.setdefault(target, []).append(name)
        return hits, dropped


def _card_text(catalog, product_id):
    kind, ident = catalog.card[product_id]
    head = catalog.products[catalog.head(product_id)]
    parts = [catalog.series_name.get(ident) if kind == 'series' else None, head['jizhanming'], head['name_cn_en']]
    return ' '.join(part for part in parts if part)


def decide(catalog, dex, product_id):
    """(系列 word or None, the Dex targets it may read back as, matched names, why, check) for one card."""
    card = catalog.card[product_id]
    own = Counter(target for member in catalog.members[card]
                  if (target := dex.resolve(catalog.products[member]['ip_series'], catalog.products[member])))
    if len(own) == 1:
        target = next(iter(own))
        return dex.write_word(target), {target}, '', "the card's own 系列", ''
    if len(own) > 1:
        return None, set(), '', "the card's own 系列 names several IPs", 'owner picks'
    code = code_of(catalog.products[catalog.head(product_id)]['sku'])
    hits, dropped = dex.found(_card_text(catalog, product_id), code)
    for kind in ('ip', 'partner'):
        picked = {target: names for target, names in hits.items() if dex.targets[target][0] == kind}
        words = {dex.write_word(target) for target in picked}
        matched = '; '.join(sorted({name for names in picked.values() for name in names}, key=str.casefold))
        if len(words) == 1:
            short = all(len(re.sub(r'\W', '', name)) <= 3 and name not in SHORTHAND
                        for names in picked.values() for name in names)
            return words.pop(), set(picked), matched, f'name names one {kind}', 'short name' if short else ''
        if len(words) > 1:
            return None, set(), matched, f'name names several {kind}s', 'owner picks'
    code_word = NO_IP_CODES.get(code)
    if code_word:
        return None, set(), '', f'{code_word}: no IP expected', ''
    why = 'no IP in the name' + (" (one is another company's)" if dropped else '')
    return None, set(), '; '.join(dropped), why, 'owner picks'


def plan(con, catalog):
    dex = Dex(con, catalog)
    rows, decided = [], {}
    for product_id, product in catalog.products.items():
        if catalog.card_ip(product_id) is not None or (product['ip_series'] or '').strip() not in EMPTY:
            continue
        card = catalog.card[product_id]
        if card not in decided:
            decided[card] = decide(catalog, dex, product_id)
        word, targets, matched, why, check = decided[card]
        back = dex.resolve(word, product) if word else None
        if word and back not in targets:
            why, check = f"{why}, but {word} reads back as {back or 'nothing'}", 'owner picks'
            word = None
        rows.append({'action': 'change' if word else 'list', 'why': why, 'check': check, 'product_id': product_id,
                     'sku': product['sku'], 'name': catalog.name(product_id), 'before': product['ip_series'],
                     'after': word or product['ip_series'], 'dex': dex.targets[back][1] if word else '',
                     'matched': matched, 'card': catalog.products[catalog.head(product_id)]['sku']})
    return rows


def main(argv=None):
    return catalog_fix.main(argv, doc=__doc__, field='ip_series', plan=plan,
                            out='ip-fill', prefix='ip-fill', columns=COLUMNS)


if __name__ == '__main__':
    sys.exit(main())
