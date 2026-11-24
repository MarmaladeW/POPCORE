"""A misspelt word still finds the product (popcore-work#32).

Every product search reads the same text of a product (catalog_identity.product_search_text_sql).
A typed word found in that text of any product is *known* and is searched exactly as typed, so a
search that already finds products keeps finding the same ones. Only a word found nowhere, made
only of a-z/0-9, is replaced by the closest words the products do carry. The rules are the ones
the website search uses (popcore.ca, popcore-theme/inc/search-match.php); see docs/product-search.md.
"""
import re
import time
import unicodedata
from itertools import islice, product as combinations

from rapidfuzz.distance import OSA

from catalog_identity import product_search_text_sql

KEEP = 3                 # replacements kept for one misspelt word
VOCABULARY_SECONDS = 60  # how long the word list of one database is kept
RESPELLINGS_LIMIT = 9    # replaced texts tried for one typed text

_PLAIN = re.compile(r'[a-z0-9]+')
_WORD = re.compile(r'[a-z0-9]{4,}')
_ACCENTED = re.compile('[À-ɏḀ-ỿ]')
_OTHER_LETTERS = {'ß': 'ss', 'æ': 'ae', 'œ': 'oe', 'ø': 'o'}
_APOSTROPHES = str.maketrans('', '', "'’‘`")
_vocabulary = {}  # database file -> (kept until, {word: products carrying it})


def _unaccented(match):
    letter = match.group()
    base = unicodedata.normalize('NFD', letter)[0]
    return base if 'a' <= base <= 'z' else _OTHER_LETTERS.get(letter, letter)


def fold(text):
    """Text as the rules compare it: NFKC, lower case, accents off Latin letters, apostrophes gone."""
    text = unicodedata.normalize('NFKC', text or '').lower()
    return _ACCENTED.sub(_unaccented, text).translate(_APOSTROPHES)


def typo_words(word, vocabulary):
    """The products' words closest to a misspelt one, closest first, at most KEEP.

    vocabulary is {word: how many products carry it}. A slip is a wrong, missing or extra letter,
    or two neighbours swapped. Up to 3 letters nothing is offered; 4 letters allow only a missing
    letter or a swap ("park" is not "pack"); 5 to 7 letters one slip; 8 and more two slips, the
    word found at most one letter shorter ("creature" is not "create"). From 5 letters on the
    start of a longer word with the same first letter counts too, within one slip, because staff
    are still typing ("sjull" on the way to "skullpanda")."""
    length = len(word)
    allowed = 0 if length <= 3 else 1 if length <= 7 else 2
    if not allowed or not _PLAIN.fullmatch(word):
        return []
    found = []
    for candidate, carried in vocabulary.items():
        size = len(candidate)
        if size < 4 or not _PLAIN.fullmatch(candidate):
            continue
        slips = OSA.distance(word, candidate, score_cutoff=allowed)
        whole = True
        if slips > allowed:
            if length < 5 or size <= length or candidate[0] != word[0]:
                continue
            slips = OSA.distance(word, candidate[:length], score_cutoff=1)
            whole = False
            if slips > 1:
                continue
        elif size < length - 1:
            continue
        elif length == 4 and not (size == 5 or (size == 4 and sorted(word) == sorted(candidate))):
            continue
        found.append((slips, candidate[0] != word[0], not whole, -carried, candidate))
    found.sort()
    # Only the words tied with the closest on slips and first letter are kept.
    return [row[-1] for row in found if row[:2] == found[0][:2]][:KEEP]


def load_vocabulary(con):
    """{word: how many products carry it} for every a-z/0-9 word of 4+ letters in the products'
    search text. One scan of the products, kept for VOCABULARY_SECONDS per database file."""
    path = con.execute('PRAGMA database_list').fetchone()[2]
    kept = _vocabulary.get(path)
    if kept and kept[0] > time.monotonic():
        return kept[1]
    vocabulary = {}
    for row in con.execute(f"SELECT {product_search_text_sql('p')} FROM products p"):
        for word in set(_WORD.findall(fold(row[0]))):
            # The searches read the text as stored: a word only folding makes would find nothing.
            if word in row[0]:
                vocabulary[word] = vocabulary.get(word, 0) + 1
    if path:
        _vocabulary.clear()
        _vocabulary[path] = (time.monotonic() + VOCABULARY_SECONDS, vocabulary)
    return vocabulary


def token_alternatives(con, tokens):
    """For each typed word, the words to search instead: the word itself when a product carries
    it (asked of the products now, never of the kept word list) or when nothing is close, else
    its replacements. Words that are not a-z/0-9 once folded (Chinese, a code with a dash) and
    words of 3 letters or fewer are never replaced."""
    tokens = list(tokens)
    alternatives = [[token] for token in tokens]
    folded = [fold(token) for token in tokens]
    maybe = [i for i, word in enumerate(folded) if len(word) >= 4 and _PLAIN.fullmatch(word)]
    if not maybe:
        return alternatives
    # One scan for all the words; LIMIT keeps the text computed once per product.
    known = con.execute(
        'SELECT ' + ', '.join('MAX(t LIKE ?)' for _ in maybe)
        + f" FROM (SELECT {product_search_text_sql('p')} AS t FROM products p LIMIT -1)",
        [f'%{tokens[i]}%' for i in maybe]).fetchone()
    vocabulary = None
    for i, carried in zip(maybe, known):
        if carried:
            continue
        if vocabulary is None:
            vocabulary = load_vocabulary(con)
        alternatives[i] = typo_words(folded[i], vocabulary) or alternatives[i]
    return alternatives


def like_any(text_sql, spellings):
    """(SQL, parameters): text_sql contains one of the spellings of a typed word."""
    clause = ' OR '.join(f'{text_sql} LIKE ?' for _ in spellings)
    return (clause if len(spellings) == 1 else f'({clause})'), [f'%{spelling}%' for spelling in spellings]


def respelled(tokens, alternatives):
    """The typed words with the misspelt ones replaced, one list per combination of replacements,
    closest first; nothing when no word was replaced."""
    if alternatives == [[token] for token in tokens]:
        return []
    return [list(words) for words in islice(combinations(*alternatives), RESPELLINGS_LIMIT)]


def respellings(con, text):
    """The typed text with its misspelt words replaced (see respelled); nothing when every word is
    known or nothing is close."""
    tokens = text.split()
    return [' '.join(words) for words in respelled(tokens, token_alternatives(con, tokens))]
