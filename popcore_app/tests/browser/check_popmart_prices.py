"""POP MART 价格 POP MART prices in a real browser against the real Flask app, a disposable database and a fake POP MART
(owner, 2026-11-25, popcore-work#91): a manager sees POPCORE's prices beside POP MART CA's and US's, filters to "we
are higher", links a product whose name differs from POP MART's and unlinks it, and asks for a read now. Staff
cannot open the screen. Screens at 390 and 1440 px go to .local/popmart-prices/."""
import asyncio
import subprocess
import sys
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5199
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'popmart-prices'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.evaluate("document.querySelectorAll('.ant-message-notice-wrapper').forEach(note => note.remove())")
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_popmart_prices import PopmartPriceTests
    fixture = PopmartPriceTests()
    fixture.setUp()
    fixture.read()
    contexts = []

    async def open_as(role):
        context = await browser.new_context(viewport={'width': width, 'height': 900})
        contexts.append(context)
        await context.add_init_script(
            f"window.__FOUNDATION_AUTH={{role:'{role}',token:'{role}'}};"
            f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.store_id},code:'DT',name:'Downtown'}}))")
        page = await context.new_page()

        async def flask_boundary(route):
            parsed = urlsplit(route.request.url)
            if parsed.hostname not in ('127.0.0.1', 'localhost'):
                await route.abort(); return
            if not parsed.path.startswith('/api/'):
                await route.continue_(); return
            body = route.request.post_data_json if route.request.post_data else None
            response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                           method=route.request.method, json=body, headers=fixture.headers(role))
            await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
        await page.route('**/*', flask_boundary)
        return page

    try:
        page = await open_as('manager')
        await page.goto(BASE + '/products/popmart-prices')
        await expect(page.get_by_role('link', name='POP MART 价格 POP MART prices')).to_have_attribute('aria-current', 'page')
        table = page.locator('.pc-pm-table')
        await expect(table.locator('tbody tr')).to_have_count(4)
        await expect(page.locator('.pc-figures')).to_contain_text('我们更贵 We are higher1')
        await fits(page, f'popmart-prices-{width}.png')

        await page.get_by_role('button', name='我们更贵 We are higher').click()
        await expect(table.locator('tbody tr')).to_have_count(1)
        await expect(table).to_contain_text('LABUBU x Pronounce Wings Plush')
        await expect(table).to_contain_text('+$0.01')

        await page.get_by_role('button', name='POP MART 没有 Not at POP MART CA').click()
        row = table.locator('tbody tr', has_text='PM0102')
        await row.get_by_role('button', name='是这个吗 Is it: DIMOO Typo Series Figures?').click()
        await expect(table.locator('tbody tr')).to_have_count(0)  # linked: no longer "not at POP MART"
        await page.get_by_role('button', name='同价 Same price').click()
        row = table.locator('tbody tr', has_text='PM0102')
        await expect(row).to_contain_text('手动对应 Linked by hand')
        await expect(row).to_contain_text('$18.99')
        await row.get_by_role('button', name='取消 Unlink').click()
        await expect(table.locator('tbody tr', has_text='PM0102')).to_have_count(0)

        await page.get_by_role('button', name='现在读取 Read now').click()
        await expect(page.get_by_role('button', name='读取中 Reading…')).to_be_visible()
        await fits(page, f'popmart-prices-reading-{width}.png')

        staff = await open_as('staff')
        await staff.goto(BASE + '/products/popmart-prices')
        await expect(staff.locator('.pc-pm-table')).to_have_count(0)
        await expect(staff.get_by_role('link', name='POP MART 价格 POP MART prices')).to_have_count(0)
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
    print('PASS: POP MART prices beside POPCORE\'s, filtered, a product linked and unlinked, a read asked for; staff kept out; '
          'at 390 and 1440 px')
