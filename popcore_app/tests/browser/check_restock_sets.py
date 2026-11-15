"""补货 on a phone with a whole set on the sheet: the picker records more than was asked, the screens say
what the set becomes on the floor (1 端 = 12 盒), the floor counts more than the pick recorded, and the
floor ends with boxes and no sealed set. Real Flask and disposable data."""
import asyncio
import json
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect
from werkzeug.serving import make_server
from check_foundation import FRONTEND, HERE

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / '.local/restock-sets/browser'
API_PORT, VITE_PORT = 5065, 5199
BASE = f'http://127.0.0.1:{VITE_PORT}'
sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
from test_restock_sets_as_boxes import SetsFixture  # noqa: E402


def balances(fixture):
    with closing(fixture.connect()) as con:
        return {(row['product_id'], row['location_id'], row['disposition']): row['quantity']
                for row in con.execute('SELECT * FROM inventory_balances') if row['quantity']}


async def checks(browser, fixture):
    context = await browser.new_context(viewport={'width': 390, 'height': 844})
    await context.add_init_script("window.__FOUNDATION_AUTH={role:'staff',token:'staff'};localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    page = await context.new_page()

    async def local_only(route):
        if urlsplit(route.request.url).hostname not in ('127.0.0.1', 'localhost'):
            await route.abort()
        else:
            await route.continue_()
    await page.route('**/*', local_only)
    await page.goto(BASE + f'/restock?session_id={fixture.session_id}')
    modal = page.get_by_role('dialog').filter(has_text='补货单 #')
    await expect(modal.get_by_role('tab', name='拣货 Pick', exact=False)).to_have_attribute('aria-selected', 'true')

    # Upstairs: the sheet asked for 1 whole set, 2 came down. The picker records 2.
    set_line = modal.locator(f'li[data-row-key="{fixture.items[0]}"]')
    await expect(set_line.get_by_text('到楼下按盒上架 · 1 端 = 12 盒', exact=True)).to_be_visible()
    await set_line.get_by_role('textbox').fill('2')
    await expect(set_line.get_by_text('到楼下按盒上架 · 2 端 = 24 盒', exact=True)).to_be_visible()
    await expect(set_line.get_by_text('比申请多 1', exact=True)).to_be_visible()
    async with page.expect_response(lambda response: f'/api/restock/items/{fixture.items[0]}/pick' in response.url) as saved:
        await set_line.get_by_role('button', name='找到', exact=True).click()
    assert (await saved.value).status == 200
    await page.screenshot(path=str(OUT / 'pick-more-than-asked.png'), full_page=True)
    await modal.get_by_role('button', name='确认拣货').click()
    confirm = page.get_by_role('dialog', name='确认入店清单')
    await expect(confirm.get_by_text('楼下上架 2 端 = 24 盒', exact=True)).to_be_visible()
    await confirm.get_by_role('button', name='确认拣货').click()
    await expect(confirm).to_be_hidden()

    # On the floor: the set line says what it becomes; the loose boxes were picked as 2 and 3 are there.
    await expect(modal.get_by_role('tab', name='收货 Receive', exact=True)).to_have_attribute('aria-selected', 'true')
    await expect(modal.get_by_text('按盒上架 2 端 = 24 盒', exact=True)).to_be_visible()
    boxes = modal.get_by_label('Line 2 quantity', exact=True)
    await expect(boxes).to_have_value('2')
    await boxes.fill('3')
    await expect(modal.get_by_text('比拣货多 1', exact=False)).to_be_visible()
    await expect(modal.get_by_role('button', name='Return quantities to source')).to_be_disabled()
    await page.screenshot(path=str(OUT / 'floor-count.png'), full_page=True)
    async with page.expect_response(lambda response: response.url.endswith('/receive')) as received:
        await modal.get_by_role('button', name='Receive quantities').click()
    assert (await received.value).status == 200
    await expect(modal.get_by_role('tab', name='拣货 Pick', exact=False)).to_have_attribute('aria-selected', 'true')
    await modal.get_by_role('tab', name='收货 Receive', exact=True).click()
    await expect(modal.get_by_text('已上架 24 盒 (2 端 = 24 盒)', exact=True)).to_be_visible()
    await page.screenshot(path=str(OUT / 'landed-as-boxes.png'), full_page=True)

    # 2 sets are 24 boxes beside the 3 loose ones; no sealed set is on the floor; upstairs gave 2 sets and 3 boxes.
    assert balances(fixture) == {
        (fixture.set, fixture.back, 'saleable'): 1, (fixture.box, fixture.back, 'saleable'): 2,
        (fixture.product_id, fixture.back, 'saleable'): 6,
        (fixture.box, fixture.floor, 'saleable'): 27, (fixture.product_id, fixture.floor, 'saleable'): 4}, balances(fixture)
    sheet = fixture.sheet()
    assert [(item['requested_qty'], item['found_qty']) for item in sheet['items']] == [(1, 2), (2, 3), (5, 4)], sheet['items']
    await context.close()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    fixture = SetsFixture()
    fixture.setUp()
    server = make_server('127.0.0.1', API_PORT, fixture.app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = OUT / 'vite.mjs'
    config.write_text(f"import config from {json.dumps(str(HERE / 'vite.config.mjs'))}; export default {{...config,server:{{...config.server,port:{VITE_PORT},proxy:{{'/api':'http://127.0.0.1:{API_PORT}'}}}}}}")
    with (OUT / 'vite.log').open('w') as log:
        process = subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--config', str(config)],
                                   cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Vite test server exited')
                try:
                    _, writer = await asyncio.open_connection('127.0.0.1', VITE_PORT)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(.1)
            else:
                raise RuntimeError('Vite test server did not start')
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                try:
                    await checks(browser, fixture)
                finally:
                    await browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            fixture.tearDown()
            fixture.doCleanups()


if __name__ == '__main__':
    asyncio.run(main())
    print('PASS: a set picked above the request, 1 端 = 12 盒 on the screens, a floor count above the pick, boxes on the floor.')
