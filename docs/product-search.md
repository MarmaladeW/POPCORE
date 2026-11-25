# Product search: a misspelt word still finds the product

Owner request, popcore-work#32 (2026-11-24): staff must find a product even when a typed word is
misspelt. The rules are the same ones the website search uses (popcore.ca,
`popcore-theme/inc/search-match.php`). They live in `popcore_app/product_search.py`.

## Where products are searched

All of these read the same text of a product, `product_search_text_sql()` in
`catalog_identity.py`: SKU, 记账名, product name, the stored search text (brand, type, series text),
design name, series name and every saved alias.

| Search | Code | How it matched before, and still does for known words |
| --- | --- | --- |
| `GET /api/products/search` (product pickers, Products page) | `blueprints/products.py` `search_products` | Every typed word, then any word, then every character, then pairs of characters; ranked by `_score_product` |
| `GET /api/stock` and `GET /api/stock/export` (`q`) | `blueprints/stock.py` | Every typed word must be in the text |
| `GET /api/losses/search` (损耗) | `stock_losses.py` `search_products` | The whole typed text must be in the name, 记账名, SKU, design, alias or series; or an exact SKU or barcode |
| `GET /api/counting/search` (盘点) | `stock_counting.py` `search_targets` | As 损耗, one result per card |

## The shared rules

Text is compared folded: Unicode NFKC, lower case, accents off Latin letters, apostrophes removed.

1. A typed word is **known** when it is part of the search text of at least one product. A known
   word is searched exactly as typed. This is asked of the products at the moment of the search.
2. Only an **unknown** word made only of a–z and 0–9 may be replaced, by the closest words from the
   word list: every a–z/0–9 word of 4 letters or more in the products' search text, with how many
   products carry it. The list is read once and kept for 60 seconds.
3. A slip is one wrong, missing or extra letter, or two neighbouring letters swapped.
   - 3 letters or fewer: never replaced.
   - 4 letters: only a missing letter or a swap. Never a wrong letter: "park" is not "pack".
   - 5 to 7 letters: one slip.
   - 8 letters or more: two slips, and the word found may be at most one letter shorter
     ("creature" is not "create").
   - From 5 letters on, the start of a longer word with the same first letter also counts, within
     one slip, because staff are still typing: "sjull" finds "skullpanda".
4. The closest words win: fewer slips, then the same first letter, then a whole word before a word
   start, then the word more products carry, then a to z. Only the words tied with the best on
   slips and first letter are kept, three at most.
5. Chinese and other non-Latin words are never replaced. `/api/products/search` already forgives
   those with its per-character and pair-of-characters matching.

## How each search uses them

- `/api/products/search`: the "every typed word" step accepts any of a word's replacements. A
  product is scored as the best of the text as typed and the text with the words replaced, so the
  product that was meant ranks as it would have with the right spelling. A typed SKU (whole text,
  any case) or a scanned barcode is always in the results and comes first.
- `/api/stock`, `/api/stock/export`: each typed word accepts any of its replacements.
- `/api/losses/search`, `/api/counting/search`: matched as before; only when that finds nothing is
  the text tried again with its unknown words replaced (at most 9 respelt texts), in the same order
  as before (exact, then starts with, then contains).

## The guarantee

A search whose words are all known answers exactly the same products in the same order as before
this change. So does a search with an unknown word that nothing is close to. The one exception is
on purpose: in `/api/products/search` an exact SKU or barcode now always comes first.
`tests/test_product_search.py` holds what the four searches answered before the change
(`UnchangedResultsTests`) and checks they still answer it.

## Not covered, on purpose

- A typed word that is part of some other product's text is known, so it is never corrected, even
  if another word was meant ("angle" stays "angle" as long as one product is spelt that way).
- A word with a digit in it is never replaced. A POPCORE ID is exact or it is wrong: a mistyped
  "CL00536" is searched as typed and never turned into the IDs next to it.
- Words stored with an accent or an apostrophe are only found typed the same way, as before.
- Other names (keychain = pendant), plurals and extra words, which the website search also handles,
  are not part of this change.
- Other screens that look products up are unchanged: `GET /api/products/export`, the 补货 series
  search (`blueprints/restock.py`), the series inventory view (`inventory_catalog.py`), trade search
  (`trade_operations.py`) and the report name matcher (`matcher.py`).
