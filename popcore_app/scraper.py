"""
scraper.py – Market price scraper for POPCORE
==============================================
Sources:
  popmart_ca  – POP MART Canada  (Playwright, JS-rendered site)
  mrpen       – Mr. Pen Toronto  (Shopify JSON API)
  whoopea     – Whoopea          (Shopify JSON API)

Run standalone:
  python scraper.py                      # scrape all stores
  python scraper.py popmart_ca           # scrape one store
  python scraper.py mrpen whoopea        # scrape two stores
"""

import os
import sys
import json
import sqlite3
import asyncio
from datetime import datetime
from urllib.request import urlopen, Request as UReq
from urllib.error import URLError, HTTPError
from matcher import match_title

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, 'popcore.db')

# ─── Store definitions ────────────────────────────────────────────────────────

STORES = {
    'popmart_ca': {
        'name': 'POP MART Canada',
        'type': 'playwright',
    },
    'mrpen': {
        'name':    'Mr. Pen',
        'type':    'shopify',
        'api_url': 'https://shopmrpen.com/collections/popmart/products.json',
        'base':    'https://shopmrpen.com',
    },
    'whoopea': {
        'name':    'Whoopea',
        'type':    'shopify',
        'api_url': 'https://whoopea.com/collections/pop-mart/products.json',
        'base':    'https://whoopea.com',
    },
}

# POP MART Canada collection pages to scrape.
# The site uses /ca/collection/{id}/{slug} format.
# We start from the homepage nav to discover all collections automatically,
# then fall back to these known URLs.
POPMART_CA_SEED_URLS = [
    'https://www.popmart.com/ca/collection/10/blind-boxes',
]
POPMART_CA_HOME = 'https://www.popmart.com/ca'

SCROLL_ROUNDS   = 6    # scroll iterations per page (for infinite scroll)
PAGE_TIMEOUT    = 30_000  # ms — navigation timeout per collection page

# ─── Shopify scraper ──────────────────────────────────────────────────────────

def scrape_shopify(store_key: str) -> list:
    """
    Fetch all products from a Shopify store's public products.json API.
    Handles pagination (up to 250 per page).
    Picks the cheapest available non-set variant as the single-unit price.
    """
    cfg   = STORES[store_key]
    api   = cfg['api_url']
    base  = cfg['base']
    name  = cfg['name']
    items = []
    page  = 1

    while True:
        url = f"{api}?limit=250&page={page}"
        try:
            req = UReq(url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; POPCOREPriceBot/1.0)',
                'Accept':     'application/json',
            })
            with urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except (URLError, HTTPError) as e:
            print(f'  [{store_key}] HTTP error on page {page}: {e}')
            break
        except Exception as e:
            print(f'  [{store_key}] Unexpected error: {e}')
            break

        products = data.get('products', [])
        if not products:
            break

        for prod in products:
            title      = (prod.get('title') or '').strip()
            handle     = prod.get('handle', '')
            prod_url   = f"{base}/products/{handle}"
            variants   = prod.get('variants', [])

            # Filter out obvious multi-unit variants (sets/cases/displays)
            SET_KEYWORDS = ('SET', 'CASE', 'DISPLAY', 'CARTON', 'FULL BOX',
                            'BOX OF', 'PACK OF')
            single_vars = [
                v for v in variants
                if not any(kw in (v.get('title') or '').upper() for kw in SET_KEYWORDS)
            ]
            if not single_vars:
                single_vars = variants

            # Prefer cheapest *available* variant, else cheapest overall
            avail = [v for v in single_vars if v.get('available')]
            pool  = avail if avail else single_vars
            if not pool:
                continue

            best = min(pool, key=lambda v: float(v.get('price') or 9999))
            try:
                price = float(best.get('price') or 0)
            except (TypeError, ValueError):
                price = 0.0

            compare_raw  = best.get('compare_at_price')
            compare_price = float(compare_raw) if compare_raw else None
            in_stock      = bool(best.get('available'))
            on_sale       = bool(compare_price and compare_price > price > 0)

            items.append({
                'store_key':       store_key,
                'store_name':      name,
                'external_title':  title,
                'price_cad':       price,
                'compare_at_price': compare_price,
                'on_sale':         on_sale,
                'in_stock':        in_stock,
                'url':             prod_url,
            })

        page += 1
        if len(products) < 250:
            break

    return items


# ─── POP MART Canada scraper (Playwright) ────────────────────────────────────

async def _popmart_async() -> list:
    """
    Async Playwright scraper for POP MART Canada.

    Strategy:
      1. Navigate to the homepage; collect all collection hrefs from the nav.
      2. For each collection page, intercept JSON API responses — POP MART's
         Next.js frontend calls their backend API to load products.
      3. Parse those intercepted responses to extract product data.
      4. Fall back to DOM scraping if API interception yields nothing.
    """
    from playwright.async_api import async_playwright

    all_products: list = []
    seen_titles:  set  = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx     = await browser.new_context(
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            locale='en-CA',
            timezone_id='America/Toronto',
            viewport={'width': 1440, 'height': 900},
        )
        page = await ctx.new_page()

        # ── Step 1: discover collection URLs from homepage nav ──────────────
        collection_urls = list(POPMART_CA_SEED_URLS)
        try:
            print('  [popmart_ca] Discovering collections from homepage…')
            await page.goto(POPMART_CA_HOME, wait_until='domcontentloaded', timeout=PAGE_TIMEOUT)
            await page.wait_for_timeout(2_500)

            # Dismiss cookie / region dialog
            for sel in [
                'button:has-text("Got it")',
                'button:has-text("Accept")',
                'button:has-text("Continue")',
                'button:has-text("OK")',
                '[class*="cookie"] button',
                '[class*="dialog"] button',
                '[class*="popup"] button',
                '[class*="modal"] button',
            ]:
                try:
                    loc = page.locator(sel).first
                    if await loc.is_visible(timeout=1_500):
                        await loc.click()
                        await page.wait_for_timeout(500)
                        break
                except Exception:
                    pass

            # Extract hrefs containing '/ca/collection/'
            hrefs = await page.eval_on_selector_all(
                'a[href*="/collection/"]',
                'els => els.map(e => e.href)',
            )
            for h in hrefs:
                if '/ca/collection/' in h and h not in collection_urls:
                    # Skip "all" or overly generic pages
                    if not any(skip in h for skip in ['/all', '/new', '/sale', '/featured']):
                        collection_urls.append(h)
            print(f'  [popmart_ca] Found {len(collection_urls)} collection URLs')
        except Exception as e:
            print(f'  [popmart_ca] Homepage discovery failed ({e}), using seed URLs')

        # Deduplicate while preserving order
        seen_urls: set = set()
        uniq_urls = []
        for u in collection_urls:
            if u not in seen_urls:
                seen_urls.add(u)
                uniq_urls.append(u)

        # ── Step 2: scrape each collection ─────────────────────────────────
        for col_url in uniq_urls:
            print(f'  [popmart_ca] → {col_url}')
            captured: list = []

            async def _capture(response, _cap=captured):
                try:
                    ct = response.headers.get('content-type', '')
                    if response.status == 200 and 'json' in ct:
                        url_lower = response.url.lower()
                        if any(kw in url_lower for kw in [
                            'product', 'item', 'commodity', 'goods',
                            'list', 'search', 'categ', 'catalog',
                        ]):
                            body = await response.json()
                            _cap.append({'url': response.url, 'data': body})
                except Exception:
                    pass

            page.on('response', _capture)
            try:
                # Use 'domcontentloaded' — much faster than 'networkidle' on
                # analytics-heavy Next.js sites. We then wait for the product
                # grid to appear rather than waiting for all requests to settle.
                await page.goto(col_url, wait_until='domcontentloaded', timeout=PAGE_TIMEOUT)
                # Give JS time to start fetching products
                await page.wait_for_timeout(3_000)
            except Exception as nav_err:
                print(f'    Navigation error: {nav_err}')
                page.remove_listener('response', _capture)
                continue

            # Dismiss any overlays on collection page
            for sel in [
                'button:has-text("Got it")',
                'button:has-text("Accept")',
                '[class*="cookie"] button',
            ]:
                try:
                    loc = page.locator(sel).first
                    if await loc.is_visible(timeout=800):
                        await loc.click()
                        await page.wait_for_timeout(300)
                except Exception:
                    pass

            # Scroll to trigger lazy-load / infinite scroll
            for _ in range(SCROLL_ROUNDS):
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await page.wait_for_timeout(1_000)

            page.remove_listener('response', _capture)

            # Try parsing intercepted API data
            parsed = _parse_api_responses(captured)
            if parsed:
                print(f'    API intercept → {len(parsed)} products')
                for item in parsed:
                    if item['external_title'] not in seen_titles:
                        seen_titles.add(item['external_title'])
                        all_products.append(item)
                continue

            # Fallback: DOM scraping
            dom = await _dom_scrape(page, col_url)
            print(f'    DOM scrape → {len(dom)} products')
            for item in dom:
                if item['external_title'] not in seen_titles:
                    seen_titles.add(item['external_title'])
                    all_products.append(item)

        await browser.close()

    print(f'  [popmart_ca] Total: {len(all_products)} unique products')
    return all_products


def _parse_api_responses(captured: list) -> list:
    """
    Try to extract product listings from intercepted API JSON responses.
    POP MART's backend is Chinese-style REST: usually { code, data: { list/items/... } }.
    Handles prices in cents (> 1000) or dollars.
    """
    products = []

    for entry in captured:
        data = entry.get('data', {})

        # Unwrap common response envelopes
        for envelope in ('data', 'result', 'response'):
            if isinstance(data, dict) and envelope in data:
                data = data[envelope]
                break

        # Find the actual product list array
        items = None
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ('list', 'items', 'products', 'records',
                        'data', 'content', 'rows', 'result'):
                v = data.get(key)
                if isinstance(v, list) and v:
                    items = v
                    break

        if not items:
            continue

        found_in_this = []
        for item in items:
            if not isinstance(item, dict):
                continue

            # Title / name field
            title = (
                item.get('productName') or item.get('nameEn') or
                item.get('name')        or item.get('spuName') or
                item.get('title')       or item.get('goodsName') or
                item.get('itemName')    or ''
            ).strip()
            if not title or len(title) < 3:
                continue

            # Price — may be in cents (e.g. 1800 = $18.00) or dollars (18.00)
            def _parse_price(raw):
                if raw is None:
                    return None
                try:
                    v = float(raw)
                    return round(v / 100, 2) if v > 500 else round(v, 2)
                except (TypeError, ValueError):
                    return None

            price = _parse_price(
                item.get('price') or item.get('salePrice') or
                item.get('currentPrice') or item.get('minPrice') or
                item.get('sellPrice') or item.get('retailPrice')
            )
            if price is None or price <= 0:
                continue

            compare = _parse_price(
                item.get('originalPrice') or item.get('marketPrice') or
                item.get('linePrice')     or item.get('comparePrice') or
                item.get('originPrice')
            )

            # Stock status
            stock_raw = (
                item.get('stockStatus') or item.get('stock') or
                item.get('skuStatus')   or item.get('status') or
                item.get('soldOut')     or item.get('available') or ''
            )
            if isinstance(stock_raw, bool):
                in_stock = stock_raw
            elif isinstance(stock_raw, (int, float)):
                in_stock = float(stock_raw) > 0
            else:
                in_stock = str(stock_raw).lower() not in (
                    'out_of_stock', 'sold_out', 'soldout', '0',
                    'false', 'unavailable', 'outofstock',
                )

            # URL
            url_path = (
                item.get('url') or item.get('link') or
                item.get('productUrl') or item.get('h5Url') or
                item.get('detailUrl') or ''
            )
            if url_path.startswith('/'):
                full_url = f'https://www.popmart.com{url_path}'
            elif url_path.startswith('http'):
                full_url = url_path
            else:
                full_url = 'https://www.popmart.com/ca'

            on_sale = bool(compare and compare > price)

            found_in_this.append({
                'store_key':        'popmart_ca',
                'store_name':       'POP MART Canada',
                'external_title':   title,
                'price_cad':        price,
                'compare_at_price': compare,
                'on_sale':          on_sale,
                'in_stock':         in_stock,
                'url':              full_url,
            })

        if found_in_this:
            products.extend(found_in_this)
            break   # stop after first successful parse

    return products


async def _dom_scrape(page, collection_url: str) -> list:
    """
    DOM fallback: try common product-card selectors, extract title + price.
    POP MART's cards often show a short IP name; we also derive the title
    from the product URL slug (which always contains the full product name).
    """
    products = []

    CARD_SELECTORS = [
        '[class*="productItem"]',
        '[class*="product-item"]',
        '[class*="ProductCard"]',
        '[class*="item-card"]',
        '[class*="goods-item"]',
        '[class*="product-card"]',
        '[class*="itemCard"]',
        'li[class*="product"]',
        'div[class*="product"][class*="wrap"]',
    ]

    for card_sel in CARD_SELECTORS:
        try:
            count = await page.locator(card_sel).count()
        except Exception:
            continue
        if count < 2:
            continue

        print(f'    DOM: found {count} cards via "{card_sel}"')
        cards = page.locator(card_sel)

        for i in range(count):
            card = cards.nth(i)
            try:
                # ── URL first — slug gives us the best product title ────────
                href = ''
                slug_title = ''
                try:
                    href = (await card.locator('a').first
                            .get_attribute('href', timeout=800) or '').strip()
                    if href.startswith('/'):
                        href = f'https://www.popmart.com{href}'
                    # POP MART URL pattern: /ca/product/{id}/{name-slug}
                    m = re.search(r'/product/\d+/([^/?#]+)', href)
                    if m:
                        slug_title = m.group(1).replace('-', ' ').title()
                except Exception:
                    pass

                # ── DOM title (prefer longer text, use slug as fallback) ────
                dom_title = ''
                for t_sel in [
                    '[class*="name"]', '[class*="title"]',
                    '[class*="Name"]', '[class*="Title"]',
                    'h3', 'h4',
                ]:
                    try:
                        candidates = card.locator(t_sel)
                        n = await candidates.count()
                        for j in range(n):
                            t = (await candidates.nth(j)
                                 .text_content(timeout=600) or '').strip()
                            if t and len(t) > len(dom_title):
                                dom_title = t
                    except Exception:
                        pass

                # Pick whichever title is longer / more informative
                title = dom_title if len(dom_title) >= len(slug_title) else slug_title
                if not title or len(title) < 4:
                    continue

                # ── Price — look for a dollar value, prefer specific elems ──
                price = 0.0
                full_html = await card.inner_html(timeout=1_500)
                for p_sel in [
                    '[class*="price"]', '[class*="Price"]',
                    '[class*="amount"]', '[class*="Amount"]',
                    '[class*="money"]',
                ]:
                    try:
                        pel = card.locator(p_sel).first
                        if await pel.count() == 0:
                            continue
                        ptxt = (await pel.text_content(timeout=600) or '').strip()
                        # Match dollar amounts: $18.00 or $18
                        nums = re.findall(r'\$?\s*([\d]+(?:\.[\d]{1,2})?)', ptxt)
                        for n in nums:
                            v = float(n)
                            # Sanity-check: POP MART CA prices 5–300 range
                            if 5 <= v <= 300:
                                price = v
                                break
                        if price:
                            break
                    except Exception:
                        pass

                # Last-resort: grep dollar amounts from card HTML
                if not price:
                    all_prices = re.findall(r'\$([\d]+(?:\.[\d]{1,2})?)', full_html)
                    for ps in all_prices:
                        v = float(ps)
                        if 5 <= v <= 300:
                            price = v
                            break

                # ── Stock status ────────────────────────────────────────────
                html_lower = full_html.lower()
                in_stock = not any(kw in html_lower for kw in [
                    'sold out', 'out of stock', 'soldout', 'sold-out',
                    'coming soon', 'notify me',
                ])

                products.append({
                    'store_key':        'popmart_ca',
                    'store_name':       'POP MART Canada',
                    'external_title':   title,
                    'price_cad':        price,
                    'compare_at_price': None,
                    'on_sale':          False,
                    'in_stock':         in_stock,
                    'url':              href or collection_url,
                })

            except Exception as e:
                print(f'    DOM card error: {e}')

        if products:
            return products   # found cards; stop trying other selectors

    return products


def scrape_popmart_ca() -> list:
    """Sync entry-point: runs the async scraper and returns results."""
    # Windows needs ProactorEventLoop for Playwright
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    return asyncio.run(_popmart_async())


# ─── Database helpers ─────────────────────────────────────────────────────────

def _load_db_products(db_path: str) -> list:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute('SELECT id, sku, name_cn_en, jizhanming FROM products')
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def _log_start(db_path: str, store_key: str):
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO scrape_log (store_key, status, started_at) VALUES (?, 'running', ?)",
        (store_key, datetime.utcnow().isoformat()),
    )
    con.commit()
    con.close()


def _log_done(db_path: str, store_key: str,
              scraped: int, matched: int, error: str | None):
    now = datetime.utcnow().isoformat()
    status = 'error' if error else 'ok'
    con = sqlite3.connect(db_path)
    con.execute('''
        UPDATE scrape_log
        SET status = ?, products_scraped = ?, products_matched = ?,
            error_msg = ?, finished_at = ?
        WHERE id = (SELECT MAX(id) FROM scrape_log WHERE store_key = ?)
    ''', (status, scraped, matched, error, now, store_key))
    con.commit()
    con.close()


def _save_prices(db_path: str, matched_items: list) -> int:
    """
    Upsert market_prices rows.
    UNIQUE constraint is on (store_key, external_title) so rescraped data
    updates existing rows rather than creating duplicates.
    """
    now = datetime.utcnow().isoformat()
    con = sqlite3.connect(db_path)
    con.execute('PRAGMA journal_mode=WAL')
    cur = con.cursor()

    for item in matched_items:
        cur.execute('''
            INSERT INTO market_prices
                (store_key, store_name, external_title,
                 product_id, sku,
                 price_cad, compare_at_price, on_sale, in_stock,
                 url, match_score, scraped_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(store_key, external_title) DO UPDATE SET
                product_id       = excluded.product_id,
                sku              = excluded.sku,
                price_cad        = excluded.price_cad,
                compare_at_price = excluded.compare_at_price,
                on_sale          = excluded.on_sale,
                in_stock         = excluded.in_stock,
                url              = excluded.url,
                match_score      = excluded.match_score,
                scraped_at       = excluded.scraped_at
        ''', (
            item['store_key'],
            item['store_name'],
            item['external_title'],
            item.get('product_id'),
            item.get('sku'),
            item.get('price_cad'),
            item.get('compare_at_price'),
            1 if item.get('on_sale')   else 0,
            1 if item.get('in_stock', True) else 0,
            item.get('url'),
            item.get('match_score'),
            now,
        ))

    con.commit()
    con.close()
    return sum(1 for i in matched_items if i.get('product_id'))


# ─── Main orchestrator ────────────────────────────────────────────────────────

def run_scrape(store_keys: list | None = None,
               db_path:    str  | None = None) -> dict:
    """
    Scrape the given stores (or all if None), match products to internal DB,
    and persist results.

    Returns dict: { store_key: { scraped, matched, error } }
    """
    if db_path    is None: db_path    = DB_PATH
    if store_keys is None: store_keys = list(STORES.keys())

    db_products = _load_db_products(db_path)
    results     = {}

    for key in store_keys:
        if key not in STORES:
            print(f'Unknown store: {key}')
            continue

        cfg = STORES[key]
        print(f'\n══ {cfg["name"]} ({key}) ══')
        _log_start(db_path, key)

        try:
            if cfg['type'] == 'shopify':
                scraped = scrape_shopify(key)
            elif cfg['type'] == 'playwright':
                scraped = scrape_popmart_ca()
            else:
                continue

            print(f'  Scraped {len(scraped)} products')

            # Fuzzy-match each scraped product to an internal product
            matched = []
            for item in scraped:
                pid, sku, score = match_title(item['external_title'], db_products)
                matched.append({**item, 'product_id': pid, 'sku': sku, 'match_score': score})

            n_matched = _save_prices(db_path, matched)
            print(f'  Matched {n_matched} / {len(scraped)} to internal products')

            _log_done(db_path, key, len(scraped), n_matched, None)
            results[key] = {'scraped': len(scraped), 'matched': n_matched, 'error': None}

        except Exception as exc:
            import traceback
            err = str(exc)
            print(f'  ERROR: {err}')
            traceback.print_exc()
            _log_done(db_path, key, 0, 0, err)
            results[key] = {'scraped': 0, 'matched': 0, 'error': err}

    return results


# ─── CLI entry-point ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    keys = sys.argv[1:] or None
    print(run_scrape(keys))
