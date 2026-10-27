"""Restock picking, legacy completion and the 晚盘 store switch against the real Flask API.

Each scenario runs on its own and every failing one is reported, using disposable data.
"""
import asyncio
import json
import re
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import async_playwright, expect
from werkzeug.serving import make_server
from check_foundation import FRONTEND, HERE

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / '.local/restock-steps/browser'
API_PORT, VITE_PORT = 5061, 5198
BASE = f'http://127.0.0.1:{VITE_PORT}'
sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
from test_receiving import ReceivingFixture


class StepsFixture(ReceivingFixture):
    """DT is live (TEST-1 in pieces, PICK-BOX in boxes); MK is still legacy."""

    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|staff', ?)",
                        (self.mk,))
            back = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='upstairs'",
                               (self.store_id,)).fetchone()[0]
            self.box = con.execute(
                """INSERT INTO products (sku, name_cn_en, jizhanming, product_type, stock_form,
                                         stock_unit, identity_status)
                   VALUES ('PICK-BOX', 'Pick Box', 'Pick Box', '盲盒', 'random_box', 'box', 'verified')"""
            ).lastrowid
            con.execute('UPDATE products SET is_bestseller=1 WHERE id IN (?, ?)', (self.product_id, self.box))
            for product, quantity in ((self.product_id, 10), (self.box, 6)):
                con.execute("""INSERT INTO inventory_balances (product_id, location_id, disposition, quantity, version)
                               VALUES (?, ?, 'saleable', ?, 1)""", (product, back, quantity))
            con.execute("""INSERT INTO stock (product_id, store_id, upstairs_qty, instore_qty, claw_qty)
                           VALUES (?, ?, 5, 0, 0)""", (self.product_id, self.mk))

            def sheet(store_id, status, items):
                sid = con.execute("INSERT INTO restock_sessions(date, status, store_id) VALUES ('2026-10-26', ?, ?)",
                                  (status, store_id)).lastrowid
                return sid, [con.execute(
                    """INSERT INTO restock_items (session_id, product_id, requested_qty, warehouse_stock_snapshot)
                       VALUES (?, ?, ?, ?)""", (sid, *item)).lastrowid for item in items]
            self.draft, _ = sheet(self.store_id, 'pending', [])
            self.live_sheet, (self.piece_item, self.box_item) = sheet(
                self.store_id, 'submitted', [(self.product_id, 5, 10), (self.box, 4, 6)])
            self.legacy_sheet, (self.legacy_item,) = sheet(self.mk, 'submitted', [(self.product_id, 3, 5)])
            con.commit()

    def value(self, sql, args=()):
        with closing(self.connect()) as con:
            return con.execute(sql, args).fetchone()[0]


def session_dialog(page):
    return page.get_by_role('dialog').filter(has_text='补货单 #')


def item_row(modal, item):
    # The hidden 录入 pane keeps its own table of the same items, so look in the visible step only.
    return modal.get_by_role('tabpanel').locator(f'tr[data-row-key="{item}"]')


async def open_sheet(page, sid):
    await page.goto(BASE + f'/restock?session_id={sid}')
    modal = session_dialog(page)
    await expect(modal.get_by_role('tab', name='拣货 Pick', exact=False)).to_have_attribute('aria-selected', 'true')
    return modal


async def units_not_sets(page, fixture, requests):
    modal = await open_sheet(page, fixture.live_sheet)
    await expect(item_row(modal, fixture.box_item).get_by_role('cell', name='4 盒', exact=True)).to_be_visible()
    await expect(item_row(modal, fixture.piece_item).get_by_role('cell', name='5 个', exact=True)).to_be_visible()
    await modal.get_by_role('tab', name='录入 Request', exact=False).click()
    await expect(modal.get_by_role('tabpanel').get_by_text('4 盒', exact=True)).to_be_visible()


async def search_uses_sheet_store(page, fixture, requests):
    await page.goto(BASE + f'/restock?session_id={fixture.draft}')
    modal = session_dialog(page)
    async with page.expect_request(lambda request: '/api/products/search' in request.url) as searched:
        await modal.get_by_placeholder('搜索产品（记账名、SKU、系列…）').fill('Pick')
    query = parse_qs(urlsplit((await searched.value).url).query)
    assert query.get('store_code') == ['DT'], query


async def typed_found_quantity_survives_reload(page, fixture, requests):
    modal = await open_sheet(page, fixture.live_sheet)
    box = item_row(modal, fixture.box_item)
    piece = item_row(modal, fixture.piece_item)
    await box.get_by_role('spinbutton').fill('2')
    await piece.get_by_role('button', name='找到').click()
    await expect(piece).to_have_class(re.compile('row-found'))
    await expect(box.get_by_role('spinbutton')).to_have_value('2')
    await box.get_by_role('button', name='找到').click()
    await expect(box).to_have_class(re.compile('row-found'))
    assert fixture.value('SELECT found_qty FROM restock_items WHERE id=?', (fixture.box_item,)) == 2


async def saved_found_quantity_is_shown_and_kept(page, fixture, requests):
    saved = fixture.client.patch(f'/api/restock/items/{fixture.box_item}/pick', headers=fixture.headers(),
                                 json={'pick_status': 'found', 'found_qty': 2})
    assert saved.status_code == 200, saved.get_json()
    modal = await open_sheet(page, fixture.live_sheet)
    box = item_row(modal, fixture.box_item)
    await expect(box.get_by_role('spinbutton')).to_have_value('2')
    async with page.expect_response(lambda response: f'/api/restock/items/{fixture.box_item}/pick' in response.url):
        await box.get_by_role('button', name='找到').click()
    assert fixture.value('SELECT found_qty FROM restock_items WHERE id=?', (fixture.box_item,)) == 2


async def pick_is_confirmed_once(page, fixture, requests):
    modal = await open_sheet(page, fixture.live_sheet)
    for item in (fixture.piece_item, fixture.box_item):
        row = item_row(modal, item)
        if fixture.value('SELECT pick_status FROM restock_items WHERE id=?', (item,)) == 'pending':
            await row.get_by_role('button', name='找到').click()
            await expect(row).to_have_class(re.compile('row-found'))
    await modal.get_by_role('button', name='确认拣货').click()
    await page.get_by_role('dialog', name='确认入店清单').get_by_role('button', name='确认拣货').click()
    await expect(modal.get_by_role('tab', name='收货 Receive', exact=True)).to_have_attribute('aria-selected', 'true')
    await modal.get_by_role('tab', name='拣货 Pick', exact=False).click()
    await expect(modal.get_by_text('已确认拣货 · Pick confirmed', exact=True)).to_be_visible()
    await expect(modal.get_by_role('button', name='确认拣货')).to_have_count(0)
    await modal.get_by_role('button', name='去收货 Receive').click()
    await expect(modal.get_by_role('tab', name='收货 Receive', exact=True)).to_have_attribute('aria-selected', 'true')
    assert fixture.value("SELECT COUNT(*) FROM inventory_delivery_events WHERE action='dispatch'") == 1


async def legacy_store_completes_the_sheet(page, fixture, requests):
    modal = await open_sheet(page, fixture.legacy_sheet)
    row = item_row(modal, fixture.legacy_item)
    await row.get_by_role('button', name='找到').click()
    await expect(row).to_have_class(re.compile('row-found'))
    await modal.get_by_role('button', name='确认拣货').click()
    async with page.expect_response(lambda response: f'/api/restock/session/{fixture.legacy_sheet}/' in response.url
                                    and response.request.method == 'POST'):
        await page.get_by_role('dialog', name='确认入店清单').get_by_role('button', name='确认拣货').click()
    sheet_paths = [path for method, path in requests if f'/session/{fixture.legacy_sheet}/' in path and method == 'POST']
    assert sheet_paths == [f'/api/restock/session/{fixture.legacy_sheet}/complete'], sheet_paths
    await expect(modal.get_by_text('补货完成！', exact=False)).to_be_visible()
    stock = fixture.value("SELECT upstairs_qty || '/' || instore_qty FROM stock WHERE product_id=? AND store_id=?",
                          (fixture.product_id, fixture.mk))
    assert stock == '2/3', stock


async def evening_count_stays_with_its_store(page, fixture, requests):
    await page.goto(BASE + '/stock/count')
    inputs = page.get_by_role('spinbutton')
    await expect(inputs).to_have_count(2)
    await inputs.nth(0).fill('9')
    await inputs.nth(1).fill('3')
    submit = page.get_by_role('button', name='提交晚盘')
    await expect(submit).to_be_enabled()
    async with page.expect_response(lambda response: '/api/inventory-check/today' in response.url
                                    and 'store_code=MK' in response.url):
        await page.get_by_label('Operations store').first.select_option('MK')
    await expect(inputs).to_have_count(2)
    await expect(inputs.nth(0)).to_have_value('')
    await expect(inputs.nth(1)).to_have_value('')
    await expect(submit).to_be_disabled()
    assert fixture.value('SELECT COUNT(*) FROM inventory_checks') == 0


SCENARIOS = (
    ('8 units, not 端', units_not_sets),
    ('8 search uses the sheet store', search_uses_sheet_store),
    ('9 typed found quantity survives a reload', typed_found_quantity_survives_reload),
    ('9 saved found quantity is shown and kept', saved_found_quantity_is_shown_and_kept),
    ('1 pick is confirmed once, then Receive', pick_is_confirmed_once),
    ('7 legacy store completes the sheet', legacy_store_completes_the_sheet),
    ('10 晚盘 counts stay with their store', evening_count_stays_with_its_store),
)


async def checks(browser, fixture):
    failures = []
    for name, scenario in SCENARIOS:
        context = await browser.new_context(viewport={'width': 1280, 'height': 900})
        await context.add_init_script("window.__FOUNDATION_AUTH={role:'staff',token:'staff'};localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
        page = await context.new_page()
        requests = []

        async def local_only(route):
            if urlsplit(route.request.url).hostname not in ('127.0.0.1', 'localhost'):
                await route.abort()
            else:
                if '/api/' in route.request.url:
                    requests.append((route.request.method, urlsplit(route.request.url).path))
                await route.continue_()
        await page.route('**/*', local_only)
        try:
            await scenario(page, fixture, requests)
            print(f'ok   {name}')
        except Exception as error:  # report every scenario, then fail
            failures.append(name)
            print(f'FAIL {name}: {(str(error).strip().splitlines() or [type(error).__name__])[0]}')
            await page.screenshot(path=str(OUT / f'failed-{len(failures)}.png'), full_page=True)
        finally:
            await context.close()
    assert not failures, f'{len(failures)} scenario(s) failed: {failures}'


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    fixture = StepsFixture()
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
    print('PASS: restock units, kept and saved found quantities, one pick confirmation, legacy completion and 晚盘 per store.')
