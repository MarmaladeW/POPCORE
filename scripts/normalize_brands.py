"""Brands: one spelling for every stored brand (owner, 2026-11-20: "change all of Pop Mart into POPMART").

What changes (brands.canonical_brand holds the names):
  1. A known brand in another spelling becomes its one name: Pop Mart → POPMART, Top Toy → TOP TOY, Toyzero+ → TOYZERO+.
  2. A POP MART IP name stored as the brand (The Monsters, Molly, Space Molly 400% …) becomes POPMART, whatever the
     ID's code (owner, 2026-11-20: "Space Molly 400 is def not a brand"); a code other than PM is noted in check.
  3. A word that is not a brand (吊卡, 杂, 迪士尼, text cut off in an old sheet) or a blank brand takes the brand of the
     ID's code (BRAND_CODES in blueprints/products.py, the other way round). An ID with no code is listed, not guessed.
  4. DR is Dreams Inc.'s code. A DR product whose brand is blank, not a brand or Dreams takes SONNY ANGEL or SMISKI
     from the card's Dex IP, else its name; anything else is DREAMS (owner, 2026-11-20: "uniform all of them").
A brand whose own code differs from the ID's is kept and noted in check (PM0697 stays OTHER, owner 2026-11-20).
The CSV holds every change and every listed product; the check column marks the ones to look at.

Dry run by default: reads only. --apply first takes an online backup to --backup (a new file), then plans again and
writes in ONE transaction. Re-running changes nothing.

    POPCORE_DB_PATH=/path/popcore.db python scripts/normalize_brands.py [--out DIR] [--apply --backup /path/before.db]
"""
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))  # last: scripts/inventory_opening.py must not shadow the app's
import catalog_fix  # noqa: E402  (also puts popcore_app on the path)
from brands import BRANDS, canonical_brand  # noqa: E402
from blueprints.products import _BRAND_CODE_OF, _POPCORE_ID, BRAND_CODES  # noqa: E402

COLUMNS = ('action', 'why', 'check', 'product_id', 'sku', 'name', 'before', 'after')
IP_NAMES = {name.casefold() for name in ('The Monsters', 'Molly', 'Dimoo', 'Hirono', 'Skullpanda', 'Space Molly 400%',
                                         'Hacipupu', 'Pucky', 'Peach Riot', 'Twinkle Twinkle')}
NOT_BRANDS = {'吊卡', '杂', '迪士尼'}
CODE_BRAND = {code: canonical_brand(names[0]) for code, names in BRAND_CODES.items()}
KNOWN = set(BRANDS) | set(CODE_BRAND.values())
_SONNY = re.compile(r'sonny|天使|(?<![a-z])sa(?![a-z])')


def code_of(sku):
    match = _POPCORE_ID.fullmatch((sku or '').strip().upper())
    return match.group(1) if match and match.group(1) in BRAND_CODES else None


def _cut_off(text):
    return '…' in text or 'truncated' in text.casefold()


def _dreams(catalog, product_id):
    """(brand, why, check) for a DR product: Sonny Angel or Smiski by the card's Dex IP, else by name, else DREAMS."""
    ip = catalog.card_ip(product_id)
    if ip and canonical_brand(ip['name']) in ('SONNY ANGEL', 'SMISKI'):
        return canonical_brand(ip['name']), f"code DR, Dex IP {ip['name']}", ''
    text = ' '.join(catalog.name(member) + ' ' + (catalog.products[member]['name_cn_en'] or '')
                    for member in dict.fromkeys((product_id, catalog.head(product_id)))).casefold()
    smiski, sonny = 'smiski' in text, bool(_SONNY.search(text))
    if smiski != sonny:
        brand = 'SMISKI' if smiski else 'SONNY ANGEL'
        return brand, f'code DR, name says {brand}', 'by name'
    return 'DREAMS', 'code DR, not Sonny Angel or Smiski by Dex or name', 'check: DREAMS'


def decide(catalog, product_id):
    """(action, after, why, check) for one product; action is change, list or None (already right)."""
    product = catalog.products[product_id]
    stored = product['brand']
    text = ' '.join((stored or '').split())
    code = code_of(product['sku'])
    canonical = canonical_brand(text)
    if text and canonical == 'DREAMS' and code == 'DR':
        brand, why, check = _dreams(catalog, product_id)
        return ('change' if brand != stored else None), brand, f'Dreams → {why}', check
    if text and canonical in KNOWN:
        own = _BRAND_CODE_OF.get(canonical.lower())
        check = f'brand is {own}, ID is {code}' if own and code and own != code else ''
        return ('change' if canonical != stored else 'list' if check else None), canonical, 'spelling', check
    if text.casefold() in IP_NAMES:
        return 'change', 'POPMART', 'IP name as brand', '' if code == 'PM' else f'ID code is {code or "none"}'
    if text and text not in NOT_BRANDS and not _cut_off(text):
        return ('change' if canonical != stored else None), canonical, 'unknown brand, in capitals', 'unknown brand'
    if code is None:
        return 'list', stored, 'no brand and no ID code', 'owner picks'
    word = 'blank' if not text else 'cut-off text' if _cut_off(text) else text
    if code == 'DR':
        brand, why, check = _dreams(catalog, product_id)
        return 'change', brand, f'{word} → {why}', check if word != 'cut-off text' else (check or 'cut-off text')
    return 'change', CODE_BRAND[code], f'{word} → code {code}', 'cut-off text' if word == 'cut-off text' else ''


def plan(con, catalog):
    rows = []
    for product_id, product in catalog.products.items():
        action, after, why, check = decide(catalog, product_id)
        if action:
            rows.append({'action': action, 'why': why, 'check': check, 'product_id': product_id, 'sku': product['sku'],
                         'name': catalog.name(product_id), 'before': product['brand'],
                         'after': after if action == 'change' else product['brand']})
    return rows


def main(argv=None):
    return catalog_fix.main(argv, doc=__doc__, field='brand', plan=plan,
                            out='brand-cleanup', prefix='brand-cleanup', columns=COLUMNS)


if __name__ == '__main__':
    sys.exit(main())
