"""Real Flask Clover matching review: edit decisions, apply selected, mobile/desktop, disposable data."""
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
OUT = ROOT / '.local/clover-matching/integration'
BASE = 'http://127.0.0.1:5189'
sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
from test_clover_match_review import CloverMatchReviewTests


async def choose(page, label, text):
    await page.locator(f'.ant-select[aria-label="{label}"]').click()
    await page.keyboard.type(text)
    await page.keyboard.press('Enter')


async def checks(browser, fixture):
    context = await browser.new_context(viewport={'width': 1280, 'height': 900})
    await context.add_init_script("window.__FOUNDATION_AUTH={role:'manager',token:'manager'}")
    page = await context.new_page()
    async def local_only(route):
        if urlsplit(route.request.url).hostname not in ('127.0.0.1', 'localhost'):
            await route.abort()
        else:
            await route.continue_()
    await page.route('**/*', local_only)
    await page.goto(BASE + '/products/clover-matching')
    await expect(page.get_by_role('heading', name='Clover matching', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Open · 3', exact=True)).to_be_visible()
    await expect(page.get_by_text('Sheet decision not found on Clover: Not On Clover')).to_be_visible()

    await choose(page, 'Decision for 留着', 'Keep old product')
    await expect(page.get_by_role('row', name='留着').get_by_text('Keep old product', exact=True).last).to_be_visible()
    await choose(page, 'Decision for Test Product', 'Other Series Figures')
    test_row = page.get_by_role('row').filter(has_text='TEST-1')
    await expect(test_row.get_by_text('Edited here', exact=True)).to_be_visible()
    await expect(page.get_by_label('Select 留着', exact=True)).to_be_disabled()
    await page.screenshot(animations='disabled', path=str(OUT / 'review-1280.png'), full_page=True)

    await page.get_by_label('Select Test Product', exact=True).check()
    await page.get_by_label('Select 没用', exact=True).check()
    await page.get_by_role('button', name='Apply selected · 2', exact=True).click()
    dialog = page.get_by_role('dialog')
    await expect(dialog.locator('.ant-modal-confirm-title')).to_have_text('Apply 2 decisions?')
    await expect(dialog.get_by_text('This cannot be undone from the app.', exact=True)).to_be_visible()
    await dialog.get_by_role('button', name='Apply decisions', exact=True).click()
    await expect(page.get_by_text('2 applied.', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Open · 1', exact=True)).to_be_visible()
    with closing(fixture.connect()) as con:
        assert con.execute('SELECT COUNT(*) FROM products WHERE id IN (?,?)', (fixture.product_id, fixture.unused_id)).fetchone()[0] == 0
        assert tuple(con.execute('SELECT instore_qty, upstairs_qty FROM stock WHERE product_id=?', (fixture.other_id,)).fetchone()) == (2, 10)
        assert con.execute('SELECT COUNT(*) FROM products WHERE id=?', (fixture.keep_id,)).fetchone()[0] == 1
    await page.get_by_role('button', name='Applied · 2', exact=True).click()
    await expect(page.get_by_text('merged into Other Series Figures', exact=False)).to_be_visible()
    await expect(page.get_by_text('deleted', exact=True)).to_be_visible()

    for width in (390, 1280):
        await page.set_viewport_size({'width': width, 'height': 900})
        await page.get_by_role('button', name='All · 3', exact=True).click()
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), f'page scrolls sideways at {width}'
        await page.screenshot(animations='disabled', path=str(OUT / f'applied-{width}.png'), full_page=True)
    await context.close()

    staff = await browser.new_context()
    await staff.add_init_script("window.__FOUNDATION_AUTH={role:'staff',token:'staff'}")
    page = await staff.new_page()
    await page.route('**/*', local_only)
    await page.goto(BASE + '/products/clover-matching')
    await expect(page.get_by_role('heading', name='Clover matching', exact=True)).to_have_count(0)
    await staff.close()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    fixture = CloverMatchReviewTests(); fixture.setUp()
    server = make_server('127.0.0.1', 5060, fixture.app)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    config = OUT / 'vite.mjs'
    config.write_text(f"import config from {json.dumps(str(HERE / 'vite.config.mjs'))}; export default {{...config,server:{{...config.server,port:5189,proxy:{{'/api':'http://127.0.0.1:5060'}}}}}}")
    with (OUT / 'vite.log').open('w') as log:
        process = subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--config', str(config)], cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError('Vite test server exited')
                try:
                    _, writer = await asyncio.open_connection('127.0.0.1', 5189); writer.close(); await writer.wait_closed(); break
                except OSError: await asyncio.sleep(.1)
            else: raise RuntimeError('Vite test server did not start')
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                try: await checks(browser, fixture)
                finally: await browser.close()
        finally:
            process.terminate(); process.wait(timeout=10)
            server.shutdown(); server.server_close(); thread.join(timeout=5)
            fixture.tearDown(); fixture.doCleanups()


if __name__ == '__main__':
    asyncio.run(main())
    print('PASS: real Flask Clover matching edits, apply with merge/delete, refusal-free run, staff blocked, 390/1280 layout.')
