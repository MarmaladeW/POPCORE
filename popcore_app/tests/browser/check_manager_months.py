"""店长/副店长 see only this month and next in 排班 (owner, 2026-11-27: "the manager can only see the schedule for that
month, not all of them"), in a real browser against the real Flask app and a disposable database on 27 Nov 2026.
The manager's team calendar arrows stop at the window, with the reason, and the 月工时 picker opens November and
December only. An admin still moves freely."""
import asyncio
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5202
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'manager-months'
HINT = '店长只能看这个月和下个月 · Managers see this month and next only'


async def open_as(browser, fixture, width, role):
    context = await browser.new_context(viewport={'width': width, 'height': 900})
    await context.add_init_script(
        f"window.__FOUNDATION_AUTH={{role:'{role}',token:'{role}'}};"
        f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.store_id},code:'DT',name:'Downtown'}}));")
    page = await context.new_page()
    await page.clock.install(time=datetime(2026, 11, 27, 17, tzinfo=timezone.utc))  # the fixture server's clock

    async def flask_boundary(route):
        parsed = urlsplit(route.request.url)
        if parsed.hostname not in ('127.0.0.1', 'localhost'):
            await route.abort(); return
        if not parsed.path.startswith('/api/'):
            await route.continue_(); return
        body = route.request.post_data_json if route.request.post_data else None
        response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                       method=route.request.method, headers=fixture.headers(role), json=body)
        await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
    await page.route('**/*', flask_boundary)
    return context, page


async def walk(button, steps=8):
    """Tap an arrow until it stops (or `steps` times); how many taps it took."""
    for taken in range(steps):
        if await button.is_disabled():
            return taken
        await button.click()
    return steps


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_schedule_manager_months import ManagerMonthTests
    fixture = ManagerMonthTests()
    fixture.setUp()
    try:
        context, page = await open_as(browser, fixture, width, 'manager')
        await page.goto(BASE + '/schedule')
        back = page.get_by_role('button', name='Previous calendar period')
        forward = page.get_by_role('button', name='Next calendar period')
        await expect(back).to_be_visible()
        await walk(back)
        await expect(back).to_be_disabled()
        await expect(back).to_have_attribute('title', HINT)
        await page.screenshot(path=OUT / f'manager-first-{width}.png', full_page=True)
        await walk(forward, 12)
        await expect(forward).to_be_disabled()
        await page.screenshot(path=OUT / f'manager-last-{width}.png', full_page=True)

        if width < 600:  # the phone's tab bar is full: pick it from its ⋯ menu, as a person would
            await page.get_by_role('button', name='More tabs').hover()
            await page.get_by_role('option', name='月工时 Monthly report').click()
        else:
            await page.get_by_role('tab', name='月工时 Monthly report').click()
        await page.locator('.ant-picker').first.click()
        for month, closed in (('2026-09', True), ('2026-10', True), ('2026-11', False), ('2026-12', False)):
            cell = expect(page.locator(f'.ant-picker-dropdown td[title="{month}"]'))
            await (cell.to_have_class(re.compile('ant-picker-cell-disabled')) if closed
                   else cell.not_to_have_class(re.compile('ant-picker-cell-disabled')))
        await page.screenshot(path=OUT / f'manager-report-{width}.png')
        await context.close()

        context, page = await open_as(browser, fixture, width, 'admin')
        await page.goto(BASE + '/schedule')
        back = page.get_by_role('button', name='Previous calendar period')
        assert await walk(back, 5) == 5, 'an admin moves back freely'
        await expect(back).to_be_enabled()
        await context.close()
    finally:
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
                    for width in (1440, 390):
                        await run(browser, width)
                finally:
                    await browser.close()
        finally:
            process.terminate(); process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
    print('PASS: manager months — the manager\'s calendar arrows stop at November and December 2026 with the reason, '
          '月工时 opens November and December only; an admin moves freely; at 1440 and 390 px')
