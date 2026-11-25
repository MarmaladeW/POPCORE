"""One spelling per brand (owner, 2026-11-20: "change all of Pop Mart into POPMART").

Every place that saves a brand passes it through canonical_brand, so "Pop Mart", "pop mart" and "POP-MART" are all
stored as POPMART. scripts/normalize_brands.py brought the stored brands to these names once."""
import re
import unicodedata

BRANDS = ('POPMART', 'SONNY ANGEL', 'SMISKI', 'DREAMS', 'HITOY', 'TOP TOY', 'TOYZERO+', 'BABY THREE',
          'FINDING UNICORN', 'VIPO', 'POKEMON', 'MOETCH', 'LETSVAN', 'ACCESSORIES', 'GIFT CARDS', 'OTHER')
# Other spellings seen or likely, beyond case, spaces, dashes and dots (which never count).
VARIANTS = {
    'DREAMS': ('dreams inc',),
    'TOYZERO+': ('toyzero', 'toyzero plus', 'toyzeroplus'),
    'BABY THREE': ('娃三岁',),
    'POKEMON': ('pokémon',),
    'ACCESSORIES': ('accessory',),
    'GIFT CARDS': ('gift card',),
    'OTHER': ('others',),
}


def _key(name):
    """The name with case, spaces, dashes, dots and apostrophes folded away: 'Pop-Mart' and 'POP MART' meet."""
    return re.sub(r"[\s\-_.'’]+", '', unicodedata.normalize('NFKC', name).casefold())


_CANONICAL = {_key(brand): brand for brand in BRANDS}
_CANONICAL.update({_key(variant): brand for brand, variants in VARIANTS.items() for variant in variants})


def canonical_brand(name):
    """The one stored spelling of a brand: a known brand in any spelling becomes its name in BRANDS; any other brand is
    kept, trimmed, with single spaces and in capitals. Blank stays ''."""
    text = ' '.join(str(name or '').split())
    if not text:
        return ''
    return _CANONICAL.get(_key(text), text.upper())
