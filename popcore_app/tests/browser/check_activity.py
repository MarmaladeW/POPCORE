"""动态 Activity in a real browser against the real Flask routes and a disposable database (owner, 2026-11-20: "in
WeChat it is super easy to check who did what … account tracking/leaving mark is really important"): what staff and
a manager do leaves one line each with their name. Only the admin sees 动态 (owner, 2026-11-26): staff and managers
have no menu entry and the page refuses them; the admin reads every line, filters by person and kind, and opens a
line to see exactly what it changed (owner, 2026-11-28): a product's changed fields before → after, the stock moved
from place to place with each count, a plain "no details" for a line that kept none, and the record a tap away. The
sheet closes with the phone's back. Nothing scrolls sideways, at 390 and 1440 px."""
import json
import asyncio
import re
import subprocess
import sys
from contextlib import closing
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

from check_foundation import FRONTEND, HERE, ROOT

PORT = 5193
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'activity'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_staff_rollout import ActivityLogTests
    fixture = ActivityLogTests('test_each_change_leaves_one_line_and_a_retry_leaves_none')
    fixture.setUp()
    contexts = []

    async def open_as(role):
        context = await browser.new_context(viewport={'width': width, 'height': 900})
        contexts.append(context)
        await context.add_init_script(
            f"window.__FOUNDATION_AUTH={{role:'{role.split(':')[0]}',token:'{role}'}};"
            f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.store_id},code:'DT',name:'Downtown'}}));")
        page = await context.new_page()

        async def flask_boundary(route):
            parsed = urlsplit(route.request.url)
            if parsed.hostname not in ('127.0.0.1', 'localhost'):
                await route.abort(); return
            if not parsed.path.startswith('/api/'):
                await route.continue_(); return
            body = {'json': route.request.post_data_json} if route.request.post_data else {}
            response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                           method=route.request.method,
                                           headers={'Authorization': f'Bearer {role}'}, **body)
            data = response.get_data()
            response.close()
            await route.fulfill(status=response.status_code, body=data, content_type=response.content_type)
        await page.route('**/*', flask_boundary)
        return page

    try:
        # Staff start a restock sheet, the helper adds to it, a manager takes a checkout (a money line).
        with closing(fixture.connect()) as con:
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|helper', ?)", (fixture.store_id,))
            con.commit()
        sheet = fixture.client.post('/api/restock/sessions', json={'store_code': 'DT'}, headers=fixture.headers('staff'))
        assert sheet.status_code == 201, sheet.get_json()
        added = fixture.client.post('/api/restock/items', json={'session_id': sheet.get_json()['id'],
                                                                'product_id': fixture.product_id, 'requested_qty': 2},
                                    headers=fixture.headers('staff:helper'))
        assert added.status_code < 400, added.get_json()
        managed = fixture.call('', fixture.body('manager-order'), 'manager-order', 'manager')
        assert managed.status_code == 201, managed.get_json()
        with closing(fixture.connect()) as con:
            lines = [tuple(row) for row in con.execute('SELECT endpoint, actor_name FROM activity_log ORDER BY id')]
        assert [line[0] for line in lines] == ['restock.create_restock_session', 'restock.add_restock_item',
                                               'checkout.create'], lines

        # Staff and managers: no 动态 in the menu, and the page itself refuses them.
        for role in ('staff', 'manager'):
            refused = await open_as(role)
            await refused.goto(BASE + '/activity')
            await expect(refused.get_by_text('这一页没有对你开放')).to_be_visible()
            await expect(refused.locator('.pc-act-row')).to_have_count(0)
            if width >= 768:
                await expect(refused.locator('.pc-rail')).to_be_visible()
                await expect(refused.locator('.pc-rail').get_by_role('link', name=re.compile('动态'))).to_have_count(0)
            await fits(refused, f'{role}-{width}.png')

        page = await open_as('admin')
        await page.goto(BASE + '/activity')
        feed = page.get_by_label('Activity', exact=True)
        rows = feed.locator('.pc-act-row')
        # Every line, the manager's money line first (newest).
        await expect(rows).to_have_count(3)
        await expect(rows.nth(0)).to_contain_text('买单 Checkout')
        await expect(rows.nth(0)).to_contain_text('Manager')
        await expect(rows.nth(1)).to_contain_text('Photo Helper')
        await expect(rows.nth(1)).to_contain_text('补货加货 Added to a restock sheet')
        await expect(rows.nth(2)).to_contain_text('Staff Cashier')
        await expect(rows.nth(2)).to_contain_text('新建补货单 Started a restock sheet')
        await fits(page, f'admin-{width}.png')
        if width >= 768:
            await expect(page.locator('.pc-rail').get_by_role('link', name=re.compile('动态'))).to_be_visible()
        # Only the kinds that happened show.
        chips = page.get_by_role('group', name='Kind')
        await expect(chips.get_by_role('button')).to_have_text(['全部 All', '库存 Stock', '买单 Checkout'])
        # Filter by person, then by kind.
        await page.get_by_role('combobox', name='Person').click()
        await page.locator('.ant-select-item-option', has_text='Photo Helper').click()
        await expect(rows).to_have_count(1)
        await chips.get_by_role('button', name='库存 Stock').click()
        await expect(rows).to_have_count(1)
        await chips.get_by_role('button', name='全部 All').click()
        await expect(rows).to_have_count(1)
        # A line opens what it changed. These restock lines changed no record or stock: the sheet says so plainly,
        # and links on to the record.
        await rows.first.click()
        sheet = page.get_by_role('dialog')

        async def closed():
            # The sheet and its fading backdrop both gone, or the next tap lands on the backdrop.
            await expect(sheet).to_be_hidden()
            await expect(page.locator('.pc-actd .ant-drawer-mask')).to_be_hidden()
        await expect(sheet).to_contain_text('补货加货 Added to a restock sheet')
        await expect(sheet).to_contain_text('Photo Helper')
        await expect(sheet).to_contain_text('这条没有保存改动细节')
        await expect(page).to_have_url(re.compile(r'/activity\?entry=\d+$'))
        await fits(page, f'admin-none-{width}.png')
        # The phone's back closes the sheet, staying on 动态.
        await page.go_back()
        await closed()
        await expect(page).to_have_url(re.compile(r'/activity$'))
        await rows.first.click()
        await sheet.get_by_role('link', name='打开 Open').click()
        await expect(page).to_have_url(re.compile(r'/restock$'))

        # A manager changes a product's price and notes (the form sends every field); a stock move lands.
        with closing(fixture.connect()) as con:
            con.execute("UPDATE products SET price=19.99, notes=NULL WHERE id=?", (fixture.product_id,))
            con.commit()
        edited = fixture.client.patch(f'/api/products/{fixture.product_id}', headers=fixture.headers('manager'),
                                      json={'price': 20.99, 'notes': '新到 New in', 'jizhanming': 'Test Product'})
        assert edited.status_code == 200, edited.get_json()
        from activity import _business_date
        with closing(fixture.connect()) as con:
            con.execute("""INSERT INTO activity_log(business_date, store_id, actor_sub, actor_name, kind, endpoint, summary,
                                                    detail, link, changes)
                           VALUES (?, ?, 'auth0|staff', 'Staff Cashier', 'stock', 'stock_moves.stock_move_create',
                                   '移货 Moved stock · 楼上 → 楼下', 'Echo 1端', '/move', ?)""",
                        (_business_date(), fixture.store_id, json.dumps({'v': 1, 'stock': [
                            {'product': 'ECHO-SET Echo 整端', 'qty': 1, 'from': {'place': 'DT 楼上', 'before': 2, 'after': 1},
                             'to': {'place': 'DT 楼下', 'before': 28, 'after': 40}, 'into': 'ECHO Echo ×12',
                             'why': '移货 楼上 → 楼下'},
                            {'product': 'BEAR Bear 吊卡', 'qty': 3, 'from': {'place': 'DT 楼下', 'before': 3, 'after': 0},
                             'to': {'place': '卖出 Sold'}, 'why': '日报 Daily report'}]}, ensure_ascii=False)))
            con.commit()
        await page.goto(BASE + '/activity')
        await expect(rows).to_have_count(5)
        await rows.nth(0).click()
        await expect(sheet).to_contain_text('移货 Moved stock · 楼上 → 楼下')
        await expect(sheet.get_by_text('2 行 lines')).to_be_visible()
        first = sheet.locator('.pc-actd-line').first
        await expect(first).to_contain_text('ECHO-SET Echo 整端')
        await expect(first).to_contain_text('×1')
        ends = first.locator('.pc-actd-end')
        await expect(ends.locator('.pc-actd-end-word')).to_have_text(['从', '到'])
        await expect(ends.locator('.pc-actd-place')).to_have_text(['DT 楼上', 'DT 楼下ECHO Echo ×12'])
        await expect(ends.locator('.pc-actd-count')).to_have_text([re.compile(r'^2 → .*1$'), re.compile(r'^28 → .*40$')])
        sold = sheet.locator('.pc-actd-line').nth(1).locator('.pc-actd-end').nth(1)
        await expect(sold).to_have_text('到卖出 Sold')   # outside the stock: no count
        await expect(sheet.locator('.pc-actd-why')).to_have_count(2)   # two reasons: each heads its lines
        await fits(page, f'admin-stock-{width}.png')
        await page.go_back()
        await closed()
        await rows.nth(1).click()
        await expect(sheet).to_contain_text('改产品 Edited a product · TEST-1')
        fields = sheet.locator('.pc-actd-record dl')
        await expect(fields.locator('dt')).to_have_text(['价格 Price', '备注 Notes'])
        await expect(fields.locator('dd').nth(0)).to_contain_text('$19.99')
        await expect(fields.locator('dd').nth(0).locator('.pc-actd-after')).to_have_text('$20.99')
        await expect(fields.locator('dd').nth(1).locator('.pc-actd-after')).to_have_text('新到 New in')
        await fits(page, f'admin-product-{width}.png')
    finally:
        for context in contexts:
            await context.close()
        fixture.doCleanups()
        fixture.tearDown()


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
    print('PASS: 动态 Activity: each change leaves a named line; staff and managers have no 动态 (menu or page); the '
          'admin reads every line, filters by person and kind, and opens a line to see what it changed (fields '
          'before → after, stock from → to with counts, or plainly none) and the record; at 390 and 1440 px')
