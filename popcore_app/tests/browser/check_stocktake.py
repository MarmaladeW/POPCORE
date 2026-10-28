"""盘点 Stocktake in a real browser against the real Flask app and a disposable database:
search, one card with its sealed set and designs, the designs check, a save whose response is
lost and retried under the same key, the scanner, an unverified item, what is left to count,
a manager fixing designs, going live from the saved counts, and a recount that corrects stock.
Also what a card must never do: save a count it only showed, overwrite someone else's newer
count, open the previous search's match on Enter, or change live stock from a card opened
before the store went live."""
import asyncio
import re
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5197
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'stocktake'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_stock_counting import BARCODE, STAFF, StockCountingTests
    fixture = StockCountingTests()
    fixture.setUp()
    with closing(fixture.connect()) as con:
        con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)", (fixture.dt,))
        con.commit()
    state = {'role': 'staff', 'saves': [], 'lose_next_save': False}
    context = await browser.new_context(viewport={'width': width, 'height': 900})
    await context.add_init_script(
        "window.__FOUNDATION_AUTH={role:'staff',token:'staff'};"
        f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.dt},code:'DT',name:'Downtown'}}))")
    page = await context.new_page()

    async def flask_boundary(route):
        parsed = urlsplit(route.request.url)
        if parsed.hostname not in ('127.0.0.1', 'localhost'):
            await route.abort(); return
        if not parsed.path.startswith('/api/'):
            await route.continue_(); return
        headers = fixture.headers(state['role'])
        key = route.request.headers.get('idempotency-key')
        if key:
            headers['Idempotency-Key'] = key
        body = route.request.post_data_json if route.request.post_data else None
        response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                       method=route.request.method, headers=headers, json=body)
        if parsed.path == '/api/counting/counts':
            state['saves'].append((body, key, response.status_code))
            if state['lose_next_save']:
                state['lose_next_save'] = False
                await route.abort('failed'); return
        await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
    await page.route('**/*', flask_boundary)

    def rows(sql, *params):
        with closing(fixture.connect()) as con:
            return [tuple(row) for row in con.execute(sql, params)]

    try:
        await page.goto(BASE + '/count')
        places = page.get_by_role('navigation', name='Count location')
        await expect(places.get_by_role('button', name=re.compile('^店内 Floor'))).to_have_attribute('aria-pressed', 'true')
        await expect(page.get_by_text('盘点中 · Counts are saved; stock changes when the store goes live.')).to_be_visible()
        search = page.get_by_label('Search or scan a product')

        # A design's name finds its blind box; the card holds the box, its sealed set and its designs.
        await search.fill('rider')
        result = page.get_by_role('button', name=re.compile('^Warmth 盲盒'))
        await expect(result).to_contain_text('盲盒 · 2 款 · 整端')
        await fits(page, f'search-{width}.png')
        await result.click()
        card = page.get_by_role('region', name='Count Warmth 盲盒')
        await expect(card.get_by_label(re.compile('^Rider'))).to_be_disabled()
        await card.get_by_label(re.compile('^整端 Whole sets')).fill('1')
        await card.get_by_label(re.compile('^散盒 Single boxes')).fill('7')
        await card.get_by_role('checkbox', name='款式正确 Designs are right').check()
        await card.get_by_label(re.compile('^Rider')).fill('2')
        await card.get_by_label(re.compile('^Lantern')).fill('0')
        await expect(card.get_by_text('合计 Total: 1 端 (12 盒) + 7 盒 · 确认款 2 个')).to_be_visible()
        await fits(page, f'card-{width}.png')

        # The first response is lost; Save again resends the same request and counts it once.
        state['lose_next_save'] = True
        await card.get_by_role('button', name='保存 Save').click()
        await expect(card.get_by_role('alert')).to_contain_text('press Save again')
        await card.get_by_role('button', name='保存 Save').click()
        saved = card.get_by_role('status')
        await expect(saved).to_contain_text('已保存 Saved')
        # POPCORE kept the whole set as boxes: the boxes here in all are compared, 7 + 1 x 12 against 5.
        await expect(saved).to_contain_text('POPCORE 5 盒 · 点到 19 盒+14')
        assert len(state['saves']) == 2 and state['saves'][0][:2] == state['saves'][1][:2] and state['saves'][0][1]
        assert rows('SELECT product_id, quantity FROM opening_counts ORDER BY product_id') == sorted(
            [(fixture.box, 7), (fixture.set, 1), (fixture.rider, 2), (fixture.lantern, 0)])
        assert rows('SELECT COUNT(*) FROM opening_count_events') == [(4,)]
        assert rows('SELECT designs_checked_by FROM product_series WHERE id=?', fixture.series) == [(STAFF,)]
        assert rows('SELECT instore_qty FROM stock WHERE product_id=? AND store_id=?', fixture.box, fixture.dt) == [(5,)]
        await fits(page, f'saved-{width}.png')

        # Next: back to the search. A scanner types the code and presses Enter; one match opens.
        await card.get_by_role('button', name='下一个 Next').click()
        await expect(search).to_be_focused()
        await search.fill(BARCODE)
        await search.press('Enter')
        card = page.get_by_role('region', name='Count Warmth 盲盒')
        await expect(card.get_by_label(re.compile('^散盒 Single boxes'))).to_have_value('7')
        await expect(card.get_by_role('checkbox', name='款式正确 Designs are right')).to_be_disabled()

        # Another phone recounts the single boxes while this card shows 7. Changing only Lantern here
        # saves only Lantern: the 7 it showed is not saved again over the other phone's 9.
        def shown(product):
            items = fixture.get('/api/counting/card', location='floor', product_id=fixture.box).get_json()['items']
            return next(item['count']['version'] for item in items if item['product_id'] == product)
        other = fixture.save([{'product_id': fixture.box, 'quantity': 9, 'count_version': shown(fixture.box)}],
                             key='other-phone-1')
        assert other.status_code == 200, other.get_json()
        await card.get_by_label(re.compile('^Lantern')).fill('1')
        await card.get_by_role('button', name='保存 Save').click()
        await expect(card.get_by_role('status')).to_contain_text('已保存 Saved')
        assert [entry['product_id'] for entry in state['saves'][-1][0]['entries']] == [fixture.lantern]
        assert rows('SELECT product_id, quantity FROM opening_counts WHERE product_id IN (?, ?) ORDER BY product_id',
                    fixture.box, fixture.lantern) == sorted([(fixture.box, 9), (fixture.lantern, 1)])
        await expect(card.get_by_label(re.compile('^散盒 Single boxes'))).to_have_value('9')

        # This time the other phone saves the boxes while this counter types boxes and Rider: the
        # boxes are refused and show the other count; Rider stays typed for the next save.
        other = fixture.save([{'product_id': fixture.box, 'quantity': 10, 'count_version': shown(fixture.box)}],
                             key='other-phone-2')
        assert other.status_code == 200, other.get_json()
        await card.get_by_label(re.compile('^散盒 Single boxes')).fill('8')
        await card.get_by_label(re.compile('^Rider')).fill('3')
        await card.get_by_role('button', name='保存 Save').click()
        await expect(card.get_by_text(re.compile('Someone else saved a count of Warmth 盲盒 while this card was open'
                                                 '.*Your other numbers are still filled in'))).to_be_visible()
        await expect(card.get_by_label(re.compile('^散盒 Single boxes'))).to_have_value('10')
        await expect(card.get_by_label(re.compile('^Rider'))).to_have_value('3')
        await card.get_by_role('button', name='保存 Save').click()
        await expect(card.get_by_role('status')).to_contain_text('已保存 Saved')
        assert rows('SELECT product_id, quantity FROM opening_counts WHERE product_id IN (?, ?) ORDER BY product_id',
                    fixture.box, fixture.rider) == sorted([(fixture.box, 10), (fixture.rider, 3)])
        await card.get_by_role('button', name='下一个 Next').click()

        # Enter right after more typing does not open the previous text's single match.
        await search.fill('echo')
        await expect(page.get_by_role('button', name=re.compile('^Echo'))).to_be_visible()
        await search.fill('echo zzz')
        await search.press('Enter')
        await expect(page.get_by_text('No product matches.', exact=False)).to_be_visible()
        await expect(page.get_by_role('region', name='Count Echo')).to_have_count(0)
        await search.fill('')

        # The product type decides: a 吊卡 is counted in pieces, without asking what it is.
        await search.fill('hanging')
        await page.get_by_role('button', name=re.compile('^Hanging Card Bear')).click()
        card = page.get_by_role('region', name='Count Hanging Card Bear')
        await expect(card.get_by_text('这是什么? What is it?')).to_have_count(0)
        await expect(card.get_by_label(re.compile('^整端 Whole sets'))).to_have_count(0)
        await card.get_by_label(re.compile('^数量 Pieces')).fill('4')
        await card.get_by_role('button', name='保存 Save').click()
        await expect(card.get_by_role('status')).to_contain_text('POPCORE 3 · 点到 4')
        await card.get_by_role('button', name='下一个 Next').click()

        # A 盲盒 with no whole set yet: whole sets and single boxes; the first whole-set count adds the set.
        await search.fill('echo')
        await page.get_by_role('button', name=re.compile('^Echo')).click()
        card = page.get_by_role('region', name='Count Echo')
        await expect(card.get_by_text('这是什么? What is it?')).to_have_count(0)
        await expect(card.get_by_label(re.compile('^每端几盒 Boxes per set'))).to_have_value('12')
        await card.get_by_label(re.compile('^整端 Whole sets')).fill('2')
        # Whole sets with 散盒 left blank would leave POPCORE's boxes here uncounted.
        await card.get_by_role('button', name='保存 Save').click()
        await expect(card.get_by_role('alert')).to_contain_text('Enter the single boxes too, 0 if there are none')
        await card.get_by_label(re.compile('^散盒 Single boxes')).fill('5')
        await expect(card.get_by_text('合计 Total: 2 端 (24 盒) + 5 盒')).to_be_visible()
        await fits(page, f'whole-set-{width}.png')
        # A boxes-per-set the counter typed is kept on the product, so it is confirmed first.
        await card.get_by_label(re.compile('^每端几盒 Boxes per set')).fill('10')
        await expect(card.get_by_text('合计 Total: 2 端 (20 盒) + 5 盒')).to_be_visible()
        saves = len(state['saves'])
        page.once('dialog', lambda dialog: asyncio.ensure_future(dialog.dismiss()))
        await card.get_by_role('button', name='保存 Save').click()
        await page.wait_for_timeout(300)
        assert len(state['saves']) == saves
        page.once('dialog', lambda dialog: asyncio.ensure_future(dialog.accept()))
        await card.get_by_role('button', name='保存 Save').click()
        await expect(card.get_by_role('status')).to_contain_text('已保存 Saved')
        assert rows("SELECT p.sku, oc.quantity FROM opening_counts oc JOIN products p ON p.id=oc.product_id "
                    "WHERE p.sku LIKE 'ECHO-1%' ORDER BY p.sku") == [('ECHO-1', 5), ('ECHO-1-SET', 2)]
        assert rows("SELECT c.output_per_input FROM product_conversions c JOIN products p ON p.id=c.source_product_id "
                    "WHERE p.sku='ECHO-1-SET'") == [(10,)]
        await expect(card.get_by_label(re.compile('^每端几盒 Boxes per set'))).to_have_count(0)
        await expect(card.get_by_label(re.compile('^整端 Whole sets'))).to_have_value('2')
        await card.get_by_role('button', name='下一个 Next').click()

        # What is left: POPCORE stock here with no count yet.
        await page.get_by_role('button', name=re.compile("What's left")).click()
        left = page.get_by_role('region', name='What is left to count')
        await expect(left.get_by_role('button', name=re.compile('^Test Product'))).to_be_visible()
        await expect(left.get_by_role('button', name=re.compile('^Warmth'))).to_have_count(0)

        # A manager adds a missing design and removes a mistyped one, from the card.
        state['role'] = 'manager'
        await page.evaluate("() => {window.__FOUNDATION_AUTH={role:'manager',token:'manager'};window.dispatchEvent(new Event('foundation-auth'))}")
        await search.fill('warmth')
        await page.get_by_role('button', name=re.compile('^Warmth 盲盒')).click()
        card = page.get_by_role('region', name='Count Warmth 盲盒')
        # Numbers typed and not saved yet stay while the manager fixes the designs.
        await card.get_by_label(re.compile('^Lantern')).fill('5')
        await card.get_by_label('Designs to add, one per line').fill('Moonlight')
        await card.get_by_role('button', name='添加 Add').click()
        await expect(card.get_by_label(re.compile('^Moonlight'))).to_be_visible()
        await expect(card.get_by_label(re.compile('^Lantern'))).to_have_value('5')
        page.once('dialog', lambda dialog: asyncio.ensure_future(dialog.accept()))
        await card.get_by_role('button', name='Remove design Moonlight').click()
        await expect(card.get_by_label(re.compile('^Moonlight'))).to_have_count(0)
        await expect(card.get_by_label(re.compile('^Lantern'))).to_have_value('5')
        await card.get_by_role('button', name='返回 Back').click()

        # A card opened before the store goes live and saved after it cannot change live stock blind.
        state['role'] = 'staff'
        await page.evaluate("() => {window.__FOUNDATION_AUTH={role:'staff',token:'staff'};window.dispatchEvent(new Event('foundation-auth'))}")
        await search.fill('warmth')
        await page.get_by_role('button', name=re.compile('^Warmth 盲盒')).click()
        card = page.get_by_role('region', name='Count Warmth 盲盒')
        await card.get_by_label(re.compile('^散盒 Single boxes')).fill('3')
        fixture._count_the_store()
        applied = fixture._go_live('--apply', '--backup', str(Path(fixture.tempdir.name) / 'before-live.db'))
        assert applied['applied'], applied.get('errors')
        await card.get_by_role('button', name='保存 Save').click()
        await expect(card.get_by_text('This store went live while the card was open. Count it again.')).to_be_visible()
        await expect(page.get_by_text('这家店已上线 · Saving a count changes this store’s stock now.')).to_be_visible()
        await expect(card.get_by_label(re.compile('^散盒 Single boxes'))).to_have_value('')
        assert rows("""SELECT b.quantity FROM inventory_balances b JOIN inventory_locations l ON l.id=b.location_id
                       WHERE b.product_id=? AND l.store_id=? AND l.code='floor' AND b.disposition='saleable'""",
                    fixture.box, fixture.dt) == [(7,)]
        # Afterwards a recount corrects stock at once.
        await card.get_by_label(re.compile('^散盒 Single boxes')).fill('6')
        await card.get_by_role('button', name='保存 Save').click()
        await expect(card.get_by_role('status')).to_contain_text('库存 7 → 6')
        # Another phone recounts the same box first: this card's save is refused, the card reloads,
        # and the warning stays up so the counter knows to count again.
        version = fixture.get('/api/counting/card', location='floor', product_id=fixture.box).get_json()['items'][0]
        other = fixture.save([{'product_id': fixture.box, 'quantity': 5, 'balance_version': version['balance_version']}],
                             key='other-phone')
        assert other.status_code == 200, other.get_json()
        await card.get_by_label(re.compile('^散盒 Single boxes')).fill('4')
        await card.get_by_role('button', name='保存 Save').click()
        stale = card.get_by_text('Stock of Warmth 盲盒 changed while you were counting (a sale or a move). Count it again.')
        await expect(stale).to_be_visible()
        await expect(card.get_by_label(re.compile('^散盒 Single boxes'))).to_have_value('')
        await page.wait_for_timeout(500)
        await expect(stale).to_be_visible()
        assert rows("""SELECT b.quantity FROM inventory_balances b JOIN inventory_locations l ON l.id=b.location_id
                       WHERE b.product_id=? AND l.store_id=? AND l.code='floor' AND b.disposition='saleable'""",
                    fixture.box, fixture.dt) == [(5,)]
        await fits(page, f'live-{width}.png')
    finally:
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
    print('PASS: stocktake search, card, designs check, retried save, scanner, unverified item, what is left, '
          'design fixes, going live from app counts and a live recount, no stale overwrites or blind live saves, '
          'at 390 and 1440 px')
