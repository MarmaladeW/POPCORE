"""搬下来了 Done in a real browser against the real Flask routes and a disposable database (owner, 2026-11-20: the small
daily actions should be handy): one person who brought a restock down themselves finishes the sheet in one tap from
录入 Request: it is submitted, picked as asked, sent and received, the whole set lands on the floor as its boxes, the
sheet shows complete, and 动态 has one line naming who brought what down; at 390 and 1440 px."""
import asyncio
import re
import subprocess
import sys
from contextlib import closing
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

from check_foundation import FRONTEND, HERE, ROOT

PORT = 5189
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'restock-one-go'


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_restock_sets_as_boxes import SetsFixture
    fixture = SetsFixture('run')
    fixture.setUp()
    context = await browser.new_context(viewport={'width': width, 'height': 900})
    try:
        with closing(fixture.connect()) as con:
            con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|staff', 'Zimo')")
            con.commit()
        sheet = fixture.client.post('/api/restock/sessions', json={'store_code': 'DT'}, headers=fixture.headers()).get_json()
        for product, quantity in ((fixture.set, 1), (fixture.box, 2)):
            added = fixture.client.post('/api/restock/items', json={'session_id': sheet['id'], 'product_id': product,
                                                                    'requested_qty': quantity}, headers=fixture.headers())
            assert added.status_code < 400, added.get_json()
        before = fixture.balances()

        await context.add_init_script(
            "window.__FOUNDATION_AUTH={role:'staff',token:'staff'};"
            f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.store_id},code:'DT',name:'Downtown'}}));")
        page = await context.new_page()

        async def flask_boundary(route):
            parsed = urlsplit(route.request.url)
            if parsed.hostname not in ('127.0.0.1', 'localhost'):
                await route.abort(); return
            if not parsed.path.startswith('/api/'):
                await route.continue_(); return
            headers = {'Authorization': 'Bearer staff'}
            for name in ('idempotency-key', 'x-popcore-quiet'):
                if route.request.headers.get(name):
                    headers[name] = route.request.headers[name]
            body = route.request.post_data_json if route.request.post_data else None
            response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                           method=route.request.method, headers=headers, json=body)
            await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
        await page.route('**/*', flask_boundary)

        await page.goto(BASE + f"/restock?session_id={sheet['id']}")
        dialog = page.get_by_role('dialog')
        await expect(dialog.get_by_role('tab', name='录入 Request', exact=False)).to_have_attribute('aria-selected', 'true')
        await dialog.get_by_role('button', name='搬下来了 Done').click()
        await page.get_by_role('dialog').filter(has_text='都搬下来了？').get_by_role('button', name='搬下来了 Done').click()
        await expect(page.get_by_text('补货完成，已上架 Restock done and on the floor')).to_be_visible(timeout=15000)
        await page.screenshot(path=OUT / f'done-{width}.png', animations='disabled')

        after = fixture.balances()
        floor_box = (fixture.box, fixture.floor, 'saleable')
        assert after.get(floor_box, 0) - before.get(floor_box, 0) == 12 + 2, (before, after)
        with closing(fixture.connect()) as con:
            status = con.execute('SELECT status FROM restock_sessions WHERE id=?', (sheet['id'],)).fetchone()[0]
            lines = [dict(row) for row in con.execute(
                "SELECT endpoint, actor_name, detail FROM activity_log WHERE endpoint LIKE 'restock.%' ORDER BY id")]
        assert status == 'completed', status
        # Starting the sheet and adding to it are lines of their own; the one tap leaves one: the arrival.
        assert [line['endpoint'] for line in lines] == [
            'restock.create_restock_session', 'restock.add_restock_item', 'restock.add_restock_item',
            'restock.restock_goods_action'], lines
        assert lines[-1]['actor_name'] == 'Zimo' and '收到 Received' in lines[-1]['detail'], lines[-1]
    finally:
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
    print('PASS: 搬下来了 Done: a restock submitted, picked, sent and received in one tap, the set on the floor as boxes, '
          'the sheet complete and one 动态 line naming who brought what down; at 390 and 1440 px')
