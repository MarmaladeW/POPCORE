"""补货 by series on a phone against the real Flask API (owner, 2026-11-03): search a product, its
series opens, choose whole sets, single boxes and designs, and the sheet lists them by series; the
page never zooms on a double tap or a focused field. Uses disposable data."""
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
OUT = ROOT / '.local/restock-series/browser'
API_PORT, VITE_PORT = 5064, 5201
BASE = f'http://127.0.0.1:{VITE_PORT}'
sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
from test_restock_series import RestockSeriesTests


def lines(fixture):
    with closing(fixture.connect()) as con:
        return dict(con.execute('SELECT product_id, requested_qty FROM restock_items WHERE session_id=?',
                                (fixture.sheet,)).fetchall())


async def fits(page, name):
    overflow = await page.evaluate('() => document.documentElement.scrollWidth - window.innerWidth')
    assert overflow <= 0, f'{name}: the page is {overflow}px wider than the screen'
    await page.wait_for_timeout(400)  # let button colour transitions finish
    await page.screenshot(path=str(OUT / name), full_page=True)


async def checks(browser, fixture, width):
    phone = width < 768
    context = await browser.new_context(viewport={'width': width, 'height': 844}, has_touch=phone, is_mobile=phone,
                                        device_scale_factor=2)
    await context.add_init_script("window.__FOUNDATION_AUTH={role:'staff',token:'staff'};localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    page = await context.new_page()

    async def local_only(route):
        if urlsplit(route.request.url).hostname not in ('127.0.0.1', 'localhost'):
            await route.abort()
        else:
            await route.continue_()
    await page.route('**/*', local_only)
    try:
        await page.goto(BASE + f'/restock?session_id={fixture.sheet}')
        modal = page.get_by_role('dialog').filter(has_text='补货单 #')
        await expect(modal.get_by_role('tab', name='录入 Request', exact=False)).to_have_attribute('aria-selected', 'true')
        # No zoom: a focused field never zooms (iOS reads maximum-scale) and a double tap never does.
        viewport = await page.get_attribute('meta[name="viewport"]', 'content')
        assert 'maximum-scale=1' in viewport, viewport
        assert await page.evaluate('getComputedStyle(document.documentElement).touchAction') == 'manipulation'
        # The sheet fills the phone and closes from its own header.
        for _ in range(30 if phone else 0):  # after its opening zoom
            box = await page.locator('.pc-restock-modal').bounding_box()
            if box and box['x'] == 0 and abs(box['width'] - width) < 1:
                break
            await asyncio.sleep(.1)
        else:
            if phone:
                raise AssertionError(f'the sheet does not fill the screen: {box}')
        await expect(modal.get_by_role('button', name='Close', exact=True)).to_be_visible()

        # A design's name finds its series once, with what is upstairs.
        await modal.get_by_placeholder('搜索系列 / 记账名 / SKU / 款式').fill('月亮')
        found = modal.get_by_role('list', name='系列 Series').get_by_role('listitem')
        await expect(found).to_have_count(1)
        await expect(found.get_by_text('楼上 2 端 · 18 盒', exact=True)).to_be_visible()
        await found.get_by_role('button').click()

        card = modal.get_by_role('region', name='补货 星星人毛绒二代')
        names = card.locator('.pc-rq-line-name')
        await expect(names).to_have_text(['整端 Whole sets', '散盒 Single boxes', '小星星', '月亮'])
        await expect(card.get_by_text('每端 6 盒', exact=True)).to_be_visible()
        add = card.get_by_role('button', name='加入 Add')
        await expect(add).to_be_disabled()
        await card.get_by_role('button', name='增加 整端 Whole sets', exact=True).click()
        await card.get_by_role('textbox', name='散盒 Single boxes', exact=True).fill('20')
        await expect(card.get_by_text('只有 14', exact=True)).to_be_visible()
        await card.get_by_role('textbox', name='散盒 Single boxes', exact=True).fill('4')
        await expect(card.get_by_text('只有 14', exact=True)).to_have_count(0)
        for _ in range(2):
            await card.get_by_role('button', name='增加 小星星', exact=True).click()
        await expect(card.get_by_text('1 端 + 4 盒 + 确认款 2 盒', exact=True)).to_be_visible()
        await fits(page, f'card-{width}.png')
        await add.click()

        group = modal.get_by_role('button', name='修改 星星人毛绒二代', exact=True)
        await expect(group).to_be_visible()
        await expect(group.locator('li')).to_have_text(['整端 Whole sets1 端', '散盒 Single boxes4 盒', '小星星2 盒'])
        assert lines(fixture) == {fixture.set: 1, fixture.box: 4, fixture.star: 2}, lines(fixture)
        await fits(page, f'sheet-{width}.png')

        # Opening it again shows what was asked; zeros take the series off the sheet in one save.
        await group.click()
        await expect(card.get_by_role('textbox', name='散盒 Single boxes', exact=True)).to_have_value('4')
        for name in ('整端 Whole sets', '散盒 Single boxes', '小星星'):
            await card.get_by_role('textbox', name=name, exact=True).fill('0')
        await card.get_by_role('button', name='移出 Remove').click()
        await expect(modal.get_by_text('还没有补货项目', exact=True)).to_be_visible()
        assert lines(fixture) == {}, lines(fixture)

        if not phone:
            return
        # Ask again, submit, and pick on the phone.
        await modal.get_by_placeholder('搜索系列 / 记账名 / SKU / 款式').fill('PM0535')
        await found.filter(has_text='星星人毛绒二代').get_by_role('button').click()
        await card.get_by_role('button', name='增加 散盒 Single boxes', exact=True).click()
        await card.get_by_role('button', name='加入 Add').click()
        await modal.get_by_role('button', name='提交 Submit').click()
        await page.locator('.ant-modal-confirm').get_by_role('button', name='确认提交').click()
        await expect(modal.get_by_role('tab', name='拣货 Pick', exact=False)).to_have_attribute('aria-selected', 'true')
        line = modal.get_by_role('tabpanel').locator('.pc-pick-line')
        await expect(line).to_have_count(1)
        await expect(line.get_by_text('散盒 Single boxes', exact=True)).to_be_visible()
        await line.get_by_role('button', name='找到', exact=True).click()
        await expect(line.get_by_role('button', name='找到', exact=True)).to_have_attribute('aria-pressed', 'true')
        await fits(page, f'pick-{width}.png')
        with closing(fixture.connect()) as con:
            picked = tuple(con.execute('SELECT pick_status, found_qty FROM restock_items WHERE session_id=?',
                                       (fixture.sheet,)).fetchone())
        assert picked == ('found', 1), picked
    finally:
        await context.close()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            for width in (390, 360, 1280):
                fixture = RestockSeriesTests()
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
                        await checks(browser, fixture, width)
                        print(f'ok   {width}px')
                    finally:
                        process.terminate()
                        process.wait(timeout=10)
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=5)
                        fixture.tearDown()
                        fixture.doCleanups()
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
    print('PASS: series search, whole sets / single boxes / designs on one card, grouped sheet, phone pick, no zoom.')
