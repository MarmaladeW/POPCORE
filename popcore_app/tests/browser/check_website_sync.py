"""网站 Website sync in a real browser against the real Flask app, a disposable database and a fake
WooCommerce (owner, 2026-11-01): an admin reads the website's products (linked by their POPCORE ID
field, never by SKU), sees what turning on would change, turns it on; a web order POPCORE cannot fill waits for review and a manager settles it;
a price goes to POPCORE's and one without a Clover item name waits until a manager OKs it (owner, 2026-11-10);
managers cannot turn the sync on and staff cannot open the page."""
import asyncio
import re
import subprocess
import sys
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5199
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'website-sync'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_website_sync import WebsiteSyncTests, _ago
    fixture = WebsiteSyncTests()
    fixture.setUp()
    # Prices: Echo's Single Box goes to its Clover price; Bear 吊卡 has no Clover item name, so it waits for an OK.
    fixture.popcore_price(fixture.box, 20.99, 'Echo Clover', boxes=12)
    fixture.popcore_price(fixture.item, 15.00)
    fixture.woo.set_price(11, '19.99')
    fixture.woo.set_price(20, '14.00')
    contexts = []

    async def open_as(role):
        context = await browser.new_context(viewport={'width': width, 'height': 900})
        contexts.append(context)
        await context.add_init_script(
            f"window.__FOUNDATION_AUTH={{role:'{role}',token:'{role}'}};"
            f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.dt},code:'DT',name:'Downtown'}}))")
        page = await context.new_page()

        async def flask_boundary(route):
            parsed = urlsplit(route.request.url)
            if parsed.hostname not in ('127.0.0.1', 'localhost'):
                await route.abort(); return
            if not parsed.path.startswith('/api/'):
                await route.continue_(); return
            body = route.request.post_data_json if route.request.post_data else None
            response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                           method=route.request.method, headers=fixture.headers(role), json=body)
            await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
        await page.route('**/*', flask_boundary)
        return page

    try:
        page = await open_as('admin')
        await page.goto(BASE + '/website')
        sync = page.get_by_role('region', name='Stock sync')
        await expect(sync.get_by_text('关 Off')).to_be_visible()
        await expect(sync.get_by_text('Not read yet')).to_be_visible()
        await expect(sync.get_by_role('button', name='开启 Turn on')).to_be_disabled()
        await sync.get_by_role('button', name='读取网站产品 Read website products').click()
        await expect(page.get_by_text('Reading the website’s products', exact=False)).to_be_visible()
        fixture.run_sync()
        await page.reload()
        await expect(sync.get_by_text('5 linked to POPCORE · 1 POPCORE ID not in POPCORE (shown sold out) · 3 without a POPCORE ID, left as they are')).to_be_visible()
        # No report since the opening count, and no sales history to hold back yet.
        await expect(sync.get_by_text('Sales in up to 2026-10-31.', exact=False)).to_be_visible()
        await expect(sync.get_by_text('Nothing held back for the shop.', exact=False)).to_be_visible()
        changes = page.get_by_role('region', name='Changes')
        await expect(changes.get_by_text('2 sell out', exact=False)).to_be_visible()
        await expect(changes.get_by_text('网站 Website 有货 in stock → POPCORE 14')).to_be_visible()
        await changes.get_by_text('3 website items have no POPCORE ID', exact=False).click()
        await expect(changes.get_by_text('SKU 6941848289407')).to_be_visible()  # a barcode, shown to find the item
        prices = page.get_by_role('region', name='Prices', exact=True)
        await expect(prices.get_by_text('网站 Website $19.99 → POPCORE $20.99')).to_be_visible()
        held = page.get_by_role('region', name='Prices waiting for an OK')
        await expect(held.get_by_text('$14.00 → $15.00: no Clover item name', exact=False)).to_be_visible()
        await fits(page, f'preview-{width}.png')
        assert fixture.woo.writes == []

        await sync.get_by_role('button', name='开启 Turn on').click()
        await expect(page.get_by_text('POPCORE will write 6 stock numbers, 1 price and 2 pickup lists', exact=False)).to_be_visible()
        await page.get_by_role('button', name='Turn on', exact=True).click()
        await expect(sync.get_by_text('开 On')).to_be_visible()
        with fixture.connect() as con:  # web orders from the last hour count
            import website_sync
            website_sync.set_state(con, 'since', _ago(60))
            website_sync.set_state(con, 'orders_after', _ago(60))
        fixture.run_sync()
        assert fixture.woo.item(11)['stock_quantity'] == 14
        assert (fixture.woo.item(11)['regular_price'], fixture.woo.item(20)['regular_price']) == ('20.99', '14.00')
        fixture.woo.order(601, [(12, 3)])  # the Whole Set option
        fixture.woo.order(602, [(20, 1)], modified=_ago(0))
        fixture.run_sync()
        await page.reload()
        review = page.get_by_role('region', name='Web orders to review')
        await expect(review.get_by_text('PM0100-SET × 3')).to_be_visible()
        await expect(review.get_by_text('库存不够', exact=False)).to_be_visible()
        recorded = page.get_by_role('region', name='Web orders')
        await expect(recorded.get_by_text('PM0200 × 1')).to_be_visible()
        await expect(recorded.get_by_text('出库 DT', exact=False)).to_be_visible()
        await fits(page, f'orders-{width}.png')

        # A manager settles the order but cannot turn the sync off.
        page = await open_as('manager')
        await page.goto(BASE + '/website')
        await expect(page.get_by_role('button', name=re.compile('Turn (on|off)'))).to_have_count(0)
        review = page.get_by_role('region', name='Web orders to review')
        await review.get_by_role('button', name='已处理 Settled').click()
        dialog = page.get_by_role('dialog')
        await expect(dialog.get_by_role('button', name='Settle')).to_be_disabled()
        await dialog.get_by_label('Reason').fill('Third set came from MK')
        await dialog.get_by_role('button', name='Settle').click()
        await expect(review.get_by_text('Every paid web order came out of stock.', exact=False)).to_be_visible()
        assert fixture.order_row(601)['state'] == 'dismissed'
        # The manager OKs the price that waits; it goes with the next write.
        held = page.get_by_role('region', name='Prices waiting for an OK')
        await held.get_by_role('button', name='确认 OK').click()
        await expect(page.get_by_text('$15.00', exact=True)).to_be_visible()
        await page.get_by_role('button', name='Send', exact=True).click()
        await expect(page.get_by_text('the new prices go with the next write', exact=False)).to_be_visible()
        fixture.run_sync()
        assert fixture.woo.item(20)['regular_price'] == '15.00'
        await page.reload()
        await expect(page.get_by_role('region', name='Prices waiting for an OK')).to_have_count(0)
        prices = page.get_by_role('region', name='Prices', exact=True)
        await prices.get_by_text('Prices POPCORE wrote lately').click()
        await expect(prices.get_by_text('$14.00 → $15.00', exact=False)).to_be_visible()
        await fits(page, f'prices-{width}.png')

        page = await open_as('staff')
        await page.goto(BASE + '/website')
        await expect(page.get_by_text("You don't have permission to view this page", exact=False)).to_be_visible()
    finally:
        for context in contexts:
            await context.close()
        fixture.tearDown()
        fixture.doCleanups()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'vite.log').open('w') as log:
        process = subprocess.Popen(['node', str(FRONTEND / 'node_modules/vite/bin/vite.js'), '--config',
                                    str(HERE / 'vite.config.mjs'), '--port', str(PORT)],
                                   cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Vite stopped')
                try:
                    _, writer = await asyncio.open_connection('127.0.0.1', PORT)
                    writer.close(); await writer.wait_closed(); break
                except OSError:
                    await asyncio.sleep(.1)
            else:
                raise RuntimeError('Vite did not start')
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    for width in (390, 1440):
                        await run(browser, width)
                finally:
                    await browser.close()
        finally:
            process.terminate(); process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
    print('PASS: website sync preview, turning on, a web order waiting and settled, a price sent and one OK\'d '
          'by a manager, managers and staff kept to their part, at 390 and 1440 px')
