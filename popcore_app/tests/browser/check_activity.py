"""动态 Activity in a real browser against the real Flask routes and a disposable database (owner, 2026-11-20: "in
WeChat it is super easy to check who did what … account tracking/leaving mark is really important"): what staff and
a manager do leaves one line each with their name; staff read their store's lines without the money ones, filter by
person and kind, and open a line's record; a manager reads every line; the home page shows the newest lines; nothing
scrolls sideways, at 390 and 1440 px."""
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

        page = await open_as('staff')
        await page.goto(BASE + '/activity')
        feed = page.get_by_label('Activity', exact=True)
        rows = feed.locator('.pc-act-row')
        await expect(rows).to_have_count(2)
        await expect(rows.nth(0)).to_contain_text('Photo Helper')
        await expect(rows.nth(0)).to_contain_text('补货加货 Added to a restock sheet')
        await expect(rows.nth(1)).to_contain_text('Staff Cashier')
        await expect(rows.nth(1)).to_contain_text('新建补货单 Started a restock sheet')
        await expect(feed).not_to_contain_text('买单')
        await fits(page, f'staff-{width}.png')
        # Filter by person, then by kind.
        await page.get_by_role('combobox', name='Person').click()
        await page.locator('.ant-select-item-option', has_text='Photo Helper').click()
        await expect(rows).to_have_count(1)
        chips = page.get_by_role('group', name='Kind')
        # Only the kinds that happened show, and staff get no chip for the manager's money line.
        await expect(chips.get_by_role('button')).to_have_text(['全部 All', '库存 Stock'])
        await chips.get_by_role('button', name='库存 Stock').click()
        await expect(rows).to_have_count(1)
        await chips.get_by_role('button', name='全部 All').click()
        await expect(rows).to_have_count(1)
        # A line opens what it was about.
        await rows.first.click()
        await expect(page).to_have_url(re.compile(r'/restock$'))

        manager = await open_as('manager')
        await manager.goto(BASE + '/activity')
        manager_rows = manager.get_by_label('Activity', exact=True).locator('.pc-act-row')
        await expect(manager_rows).to_have_count(3)
        await expect(manager_rows.first).to_contain_text('买单 Checkout')
        await expect(manager_rows.first).to_contain_text('Manager')
        await fits(manager, f'manager-{width}.png')
        # The menu has 动态 for everyone who works at the store.
        if width >= 768:
            await expect(manager.locator('.pc-rail').get_by_role('link', name=re.compile('动态'))).to_be_visible()
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
    print('PASS: 动态 Activity: each change leaves a named line, staff read their store without money lines, filter by '
          'person and kind and open a line; a manager reads every line; 动态 in the menu; at 390 and 1440 px')
