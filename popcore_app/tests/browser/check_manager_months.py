"""店长/副店长 only look at 排班, and only at the next two weeks (2026-11-28: managers do not edit the schedule and
always see the next two weeks), in a real browser against the real Flask app and a disposable database on 27 Nov 2026.
The manager's team calendar arrows stop at Nov 27 – Dec 10, with the dates; a day shows who works without
any assign or edit button, no shift dialog opens and the checklist is gone; 月工时 is their own slip only and the page
never asks for anyone else's hours; the manager has their own availability tab and no reminder editing. An admin still
moves freely and assigns."""
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
HINT = '店长只能看 2026-11-27 到 2026-12-10 的排班 · Managers see 2026-11-27 – 2026-12-10 only'
VIEW_ONLY = '只能查看 · View only: 排班由管理员安排 The admin makes the schedule'


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
        asked = []
        page.on('request', lambda request: asked.append(urlsplit(request.url).path))
        await page.goto(BASE + '/schedule')
        back = page.get_by_role('button', name='Previous calendar period')
        forward = page.get_by_role('button', name='Next calendar period')
        # The two weeks start today, so the fortnight holding today is the first one. Waiting for the arrow to turn
        # off also waits for the window to load: before it does, the arrows still move freely.
        await expect(back).to_be_disabled()
        await expect(back).to_have_attribute('title', HINT)
        await page.screenshot(path=OUT / f'manager-first-{width}.png', full_page=True)
        await walk(forward, 12)
        await expect(forward).to_be_disabled()
        await page.screenshot(path=OUT / f'manager-last-{width}.png', full_page=True)
        await expect(page.get_by_text(VIEW_ONLY, exact=True)).to_be_visible()
        await expect(page.locator('.pc-coverage-toggle')).to_have_count(0)
        if width > 600:
            await page.get_by_role('button', name='Month', exact=True).click()  # December, after the walk forward
            await expect(back).to_be_enabled()  # November still holds Nov 27–30
            await back.click()
            await expect(back).to_be_disabled()
            await expect(forward).to_be_enabled()
            await page.get_by_role('button', name='View 2026-11-30 DT', exact=True).click()
            day = page.get_by_role('region', name='Schedule details 2026-11-30 DT')
            await expect(day.get_by_text('Assigned · 1', exact=True)).to_be_visible()
            await expect(day.get_by_role('button')).to_have_count(0)
            await expect(page.get_by_role('dialog')).to_have_count(0)
            await page.locator('.pc-store-calendars .pc-shift').filter(has_text='Cashier').first.click()
            await expect(page.get_by_role('dialog')).to_have_count(0)
            await page.screenshot(path=OUT / f'manager-view-only-{width}.png', full_page=True)
            await page.get_by_role('tab', name='我的可上时间 My availability').click()
            await expect(page.get_by_role('button', name='Submit two weeks', exact=True)).to_be_visible()
            await page.get_by_role('tab', name='我的班 My shifts').click()
            await expect(page.get_by_role('button', name='Edit reminder', exact=True)).to_have_count(0)

        # 月工时 is their own slip, as staff have it (2026-11-28): no team report, nobody else's hours.
        if width < 600:  # the phone's tab bar is full: pick it from its ⋯ menu, as a person would
            await page.get_by_role('button', name='More tabs').hover()
            await page.get_by_role('option', name='月工时 My hours').click()
        else:
            await page.get_by_role('tab', name='月工时 My hours').click()
        await expect(page.get_by_role('article', name=re.compile('2026-11 hours$'))).to_be_visible()
        await expect(page.get_by_text('Monthly hours report', exact=True)).to_have_count(0)
        await expect(page.get_by_role('tab', name='月工时 Monthly report')).to_have_count(0)
        await page.get_by_role('button', name='上个月 Previous month').click()
        await expect(page.get_by_role('article', name=re.compile('2026-10 hours$'))).to_be_visible()
        await page.screenshot(path=OUT / f'manager-my-hours-{width}.png')
        reports = {path for path in asked if path.startswith('/api/schedule/reports/')}
        assert reports == {'/api/schedule/reports/monthly/me'}, reports
        assert not [path for path in asked if re.fullmatch(r'/api/schedule/employees/\d+/hours', path)], asked
        await context.close()

        context, page = await open_as(browser, fixture, width, 'admin')
        await page.goto(BASE + '/schedule')
        back = page.get_by_role('button', name='Previous calendar period')
        assert await walk(back, 5) == 5, 'an admin moves back freely'
        await expect(back).to_be_enabled()
        await expect(page.get_by_text(VIEW_ONLY, exact=True)).to_have_count(0)
        await expect(page.get_by_role('tab', name='我的可上时间 My availability')).to_have_count(0)
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
    print('PASS: manager view only — the manager\'s calendar arrows stop at Nov 27 – Dec 10 2026 with the dates, a day shows '
          'its shifts without buttons or dialogs, no checklist, 月工时 is their own slip only, own availability tab; '
          'an admin moves freely; at 1440 and 390 px')
