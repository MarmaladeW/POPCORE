"""确认款价格 Design prices in a real browser against the real Flask app, a disposable database and a fake
WooCommerce (owner, 2026-11-12): a manager finds designs by box, types prices, pastes a column copied from a
spreadsheet, sets one price for a box's designs and raises them by a percentage, treats a secret apart, reviews
what changes before saving, and saves. The website sync then sends the first prices and the small change, and
holds the secret's jump of more than 30% for a manager's OK on 网站 Website. Staff cannot open the screen."""
import asyncio
import subprocess
import sys
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5198
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'design-prices'

PASTE = """async ([label, text]) => {
  const input = document.querySelector(`input[aria-label="${label}"]`)
  const data = new DataTransfer(); data.setData('text/plain', text)
  input.focus()
  input.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }))
}"""


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.evaluate("document.querySelectorAll('.ant-message-notice-wrapper').forEach(note => note.remove())")
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_design_prices import DesignPriceTests
    fixture = DesignPriceTests()
    fixture.setUp()
    # A second box, as the Dex designs were added on 2026-10-26: each design carries the box's $26.99.
    with fixture.connect() as con:
        prince = con.execute("""INSERT INTO products(sku, jizhanming, name_cn_en, price, ip_series, brand, stock_form,
                                    stock_unit, identity_status, clover_item_name)
                                VALUES ('PM0209', '小王子', 'The Little Prince Series Figures', 26.99, 'The Little Prince',
                                        'POP MART', 'random_box', 'box', 'verified', 'Little Prince')""").lastrowid
    names = ['The Fox', 'The Rose', 'The King', 'The Pilot', 'The Little Prince-Special Edition（Super Secret）']
    response = fixture.client.post(f'/api/products/{prince}/designs', json={'design_names': names},
                                   headers={**fixture.headers('manager'), 'Idempotency-Key': 'prince'})
    assert response.status_code == 201, response.get_json()
    fixture.woo.product(90, 'PM0209-01', status='draft', price='26.99')
    fixture.woo.product(91, 'PM0209-02', status='draft', price='26.99')
    fixture.woo.product(94, 'PM0209-05', price='94.99')  # the secret, live with its collector price
    fixture.turn_on()
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
            key = route.request.headers.get('idempotency-key')
            response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                           method=route.request.method, json=body,
                                           headers={**fixture.headers(role), **({'Idempotency-Key': key} if key else {})})
            await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
        await page.route('**/*', flask_boundary)
        return page

    try:
        page = await open_as('manager')
        await page.goto(BASE + '/products/design-prices')
        await expect(page.get_by_role('link', name='确认款价格 Design prices')).to_have_attribute('aria-current', 'page')
        await expect(page.get_by_text('Saved prices go to popcore.ca within a minute', exact=False)).to_be_visible()
        await expect(page.get_by_text('盒价 · Box price, not its own').first).to_be_visible()
        def row(label):  # a design's row, found by its price input
            return page.locator('tr', has=page.get_by_label(f'New price for {label}', exact=True))
        rain = row('PM0100-03 Rain')
        await expect(rain.get_by_text('$95.00')).to_be_visible()
        await expect(rain.get_by_text('在售 Live')).to_be_visible()
        await fits(page, f'list-{width}.png')

        # Echo: Rain +4%, then a column pasted from Sun downward (Moon, then the secret).
        await page.get_by_label('New price for PM0100-03 Rain').fill('98.99')
        await expect(rain.get_by_text('+4%')).to_be_visible()
        await page.evaluate(PASTE, ['New price for PM0100-04 Sun', '60\n$30.00\n150'])
        await expect(page.get_by_text('3 prices pasted down the column', exact=False)).to_be_visible()
        await expect(page.get_by_label('New price for PM0100-05 Moon')).to_have_value('$30.00')
        star = row('PM0100-06 隐藏 Secret Star')
        await expect(star.get_by_text('+36% · 需经理确认 Waits for an OK')).to_be_visible()
        await expect(row('PM0100-04 Sun').get_by_text('首次定价 · First price')).to_be_visible()

        # The Little Prince: regular designs one price, then 10% more; its secret apart.
        await page.get_by_label('Search designs').fill('little prince')
        await expect(page.get_by_text('Rain')).to_have_count(0)
        await page.get_by_role('button', name='选常规 Regular (4)').click()
        bulk = page.get_by_role('group', name='Change the selected designs')
        await expect(bulk.get_by_text('已选 4 款 · 4 selected')).to_be_visible()
        await bulk.get_by_label('Set the selected to').fill('25.99')
        await bulk.get_by_role('button', name='应用 Apply').first.click()
        await bulk.get_by_label('Raise or lower the selected by').fill('10')
        await bulk.get_by_role('button', name='应用 Apply').nth(1).click()
        for sku, name in (('PM0209-01', 'The Fox'), ('PM0209-04', 'The Pilot')):
            await expect(page.get_by_label(f'New price for {sku} {name}')).to_have_value('28.59')
        await page.get_by_label('New price for PM0209-05 The Little Prince-Special Edition（Super Secret）').fill('99.99')
        await page.get_by_label('Search designs').fill('')
        await page.get_by_role('button', name='已改未存 Edited, not saved').click()
        await expect(page.get_by_text('9 处改动 · 9 changes')).to_be_visible()
        await fits(page, f'edited-{width}.png')

        await page.get_by_role('button', name='检查并保存 Review & save').click()
        dialog = page.get_by_role('dialog')
        await expect(dialog.get_by_text('等经理确认 · Wait for a manager’s OK')).to_be_visible()
        await expect(dialog.get_by_text('网站 $109.99 → $150.00: +36%, more than 30%', exact=False)).to_be_visible()
        await expect(dialog.get_by_text('首次定价 · First prices')).to_be_visible()
        await expect(dialog.get_by_text('网站 no price → $30.00: first price', exact=False)).to_be_visible()
        await expect(dialog.get_by_text('网站 $20.99 (draft) → $60.00: first price', exact=False)).to_be_visible()
        await expect(dialog.get_by_text('不在网站 · Not on the website')).to_be_visible()
        await fits(page, f'review-{width}.png')
        await dialog.get_by_role('button', name='保存 Save 9').click()
        await expect(page.get_by_text('已保存 9 个价格 · 9 prices saved')).to_be_visible()
        await expect(page.get_by_text('0 处改动 · 0 changes')).to_be_visible()
        fixture.woo.writes.clear()
        fixture.run_sync()
        assert {wc_id: fixture.woo.item(wc_id)['regular_price'] for wc_id in (30, 31, 32, 33, 90, 91, 94)} == {
            30: '98.99', 31: '60.00', 32: '30.00', 33: '109.99', 90: '28.59', 91: '28.59', 94: '99.99'}, fixture.woo.writes
        await page.get_by_role('button', name='今天改过 Changed today').click()
        await expect(page.get_by_text('已定价 Set', exact=False)).to_have_count(9)
        await fits(page, f'saved-{width}.png')

        # The secret's jump waits on 网站 Website for a manager's OK.
        await page.goto(BASE + '/website')
        held = page.get_by_role('region', name='Prices waiting for an OK')
        await expect(held.get_by_text('$109.99 → $150.00: +36%, a big change', exact=False)).to_be_visible()
        await fits(page, f'website-{width}.png')

        page = await open_as('staff')
        await page.goto(BASE + '/products/design-prices')
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
    print('PASS: design prices typed, pasted, set per box and raised, a secret apart, reviewed and saved; first prices '
          'and a small change sent, a jump over 30% held for an OK; staff kept out; at 390 and 1440 px')
