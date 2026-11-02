"""日报库存核对 Report stock checks in a real browser against the real Flask app and a disposable
database (owner, 2026-10-29): a live store's pasted report that the floor could not cover, and a
whole set with no set size, wait on the manager home; the manager opens them, checks one off, and
staff never see them."""
import asyncio
import re
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5198
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'report-stock'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_report_stock import LIVE_DATE, ReportStockTests
    fixture = ReportStockTests()
    fixture.setUp()
    with closing(fixture.connect()) as con:
        bare = fixture._product(con, 'BARE-SET', 'Bare 整端', 'sealed_set', 'set')
        con.commit()
    pasted = fixture.report([(fixture.item, 5), (bare, 1), (fixture.box, 2)])
    assert pasted.status_code == 200, pasted.get_json()
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
            query = parsed.query
            if parsed.path == '/api/today':
                # The fixture's report day stands in for today.
                query = re.sub(r'business_date=[^&]*', f'business_date={LIVE_DATE}', query)
            body = route.request.post_data_json if route.request.post_data else None
            response = fixture.client.open(parsed.path + ('?' + query if query else ''), method=route.request.method,
                                           headers=fixture.headers(role), json=body)
            await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
        await page.route('**/*', flask_boundary)
        return page

    def statuses():
        with closing(fixture.connect()) as con:
            return {row[0]: row[1] for row in con.execute('SELECT reason, status FROM report_stock_issues')}

    try:
        page = await open_as('manager')
        await page.goto(BASE + '/')
        strip = page.locator('.pc-hm-strip')
        await expect(strip.get_by_text('日报差异 Report stock')).to_be_visible()
        panel = page.get_by_role('region', name='Report stock checks')
        await expect(panel.get_by_text('2 条日报库存差异')).to_be_visible()
        await fits(page, f'home-{width}.png')
        await panel.get_by_role('link', name='核对 Review').click()
        await expect(page).to_have_url(re.compile('/sales/report-stock$'))

        day = page.get_by_role('region', name=f'DT {LIVE_DATE}')
        await expect(day.get_by_text('Bear 吊卡 (BEAR) × 2')).to_be_visible()
        await expect(day.get_by_text('Not enough on the floor · 楼下 Floor 0 · 楼上 Upstairs 0')).to_be_visible()
        await expect(day.get_by_text('Bare 整端 (BARE-SET) × 1')).to_be_visible()
        await expect(page.get_by_text('不知道每端几盒 Set size not recorded')).to_be_visible()
        await expect(day.get_by_role('link', name='盘点 Count')).to_have_attribute(
            'href', f'/count?location=floor&product={fixture.item}')
        await fits(page, f'checks-{width}.png')

        await day.get_by_role('button', name='Mark Bear 吊卡 checked').click()
        await expect(day.get_by_text('Bear 吊卡 (BEAR) × 2')).to_have_count(0)
        await expect(day.get_by_text('Bare 整端 (BARE-SET) × 1')).to_be_visible()
        assert statuses() == {'not_enough_on_floor': 'checked', 'set_size_unknown': 'open'}, statuses()

        # Staff never see the checks: not on their home, and the page is managers' only.
        page = await open_as('staff')
        await page.goto(BASE + '/')
        await expect(page.get_by_role('heading', name=re.compile('开始工作'))).to_be_visible()
        await expect(page.get_by_text(re.compile('日报差异|日报库存'))).to_have_count(0)
        await page.goto(BASE + '/sales/report-stock')
        await expect(page.get_by_text("You don't have permission to view this page", exact=False)).to_be_visible()
        await expect(page.get_by_text('Bare 整端 (BARE-SET) × 1')).to_have_count(0)
    finally:
        for context in contexts:
            await context.close()
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
    print('PASS: report stock checks on the manager home and their page, a check closing one, '
          'staff kept out, at 390 and 1440 px')
