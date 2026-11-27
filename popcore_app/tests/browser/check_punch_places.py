"""打卡 only at the store, by GPS (owner, 2026-11-27: "make sure they dont click 打卡 when they are not in the store
yet"; Markham has no store computer), in a real browser against the real Flask app and a disposable database.
The admin saves DT's spot standing in the store (a rough fix is refused) and MK's from a pasted map pin, then
clears MK. A staff member 1.1 km away is told how far; at the store the punch counts."""
import asyncio
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5201
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'punch-places'
DT_SPOT = {'latitude': 43.6532, 'longitude': -79.3832}


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def open_as(browser, fixture, width, role, spot):
    context = await browser.new_context(viewport={'width': width, 'height': 900})
    await context.grant_permissions(['geolocation'])
    await context.set_geolocation(spot)
    await context.add_init_script(
        f"window.__FOUNDATION_AUTH={{role:'{role}',token:'{role}'}};"
        f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.store_id},code:'DT',name:'Downtown'}}));")
    page = await context.new_page()
    await page.clock.install(time=datetime(2026, 9, 17, 2, tzinfo=timezone.utc))  # the server's clock in the fixture

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


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_schedule_attendance import AttendanceCase
    fixture = AttendanceCase()
    fixture.setUp()
    with closing(fixture.connect()) as con:
        con.execute("DELETE FROM app_settings WHERE key='punch_places'")
        con.commit()
    try:
        context, page = await open_as(browser, fixture, width, 'admin', {**DT_SPOT, 'accuracy': 400})
        await page.goto(BASE + '/settings')
        name = '打卡位置 / Punch-in'
        if width < 600:  # the phone's tab bar is full: pick it from its ⋯ menu, as a person would
            await page.locator('.ant-tabs-nav-more').hover()
            await page.get_by_role('option', name=name).click()
        else:
            await page.get_by_role('tab', name=name).click()
        panel = page.get_by_role('tabpanel', name=name)
        dt = panel.get_by_role('region', name='DT punch-in location')
        mk = panel.get_by_role('region', name='MK punch-in location')
        await expect(dt).to_contain_text('未设置')
        await expect(mk).to_contain_text('未设置')
        await fits(page, f'settings-empty-{width}.png')

        # A rough fix (±400 m) is not saved.
        await dt.get_by_role('button', name='用我现在的位置 Use where I am now').click()
        await expect(page.get_by_text('定位不够准（±400 米）')).to_be_visible()
        await expect(dt).to_contain_text('未设置')
        await context.set_geolocation({**DT_SPOT, 'accuracy': 15})
        await dt.get_by_role('button', name='用我现在的位置 Use where I am now').click()
        await expect(dt).to_contain_text('43.6532, -79.3832')
        await expect(dt.get_by_role('link')).to_have_attribute('href', 'https://www.google.com/maps?q=43.6532,-79.3832')

        await mk.get_by_role('textbox', name='MK coordinates').fill('Markham')
        await mk.get_by_role('button', name='保存坐标 Save').click()
        await expect(page.get_by_text('请粘贴像 43.6532, -79.3832 这样的坐标').first).to_be_visible()
        await mk.get_by_role('textbox', name='MK coordinates').fill('43.8561, -79.3370')
        await mk.get_by_role('button', name='保存坐标 Save').click()
        await expect(mk).to_contain_text('43.8561, -79.337')
        await fits(page, f'settings-saved-{width}.png')
        await mk.get_by_role('button', name='清除 Clear').click()
        await page.get_by_role('button', name='清除 Clear', exact=True).last.click()
        await expect(mk).to_contain_text('未设置')
        await context.close()

        # Staff 1.1 km away are told how far; at the store the punch counts.
        context, page = await open_as(browser, fixture, width, 'staff', {'latitude': 43.6632, 'longitude': -79.3832, 'accuracy': 10})
        await page.goto(BASE + '/schedule')
        attendance = page.get_by_role('region', name="Today's attendance")
        await attendance.get_by_role('button', name='Punch in', exact=True).click()
        await expect(attendance.get_by_role('alert')).to_contain_text('你离店还有 1.1 公里')
        await expect(attendance.get_by_role('button', name='Retry attendance')).to_have_count(0)
        await fits(page, f'staff-too-far-{width}.png')
        await context.set_geolocation({'latitude': 43.6533, 'longitude': -79.3831, 'accuracy': 10})
        await attendance.get_by_role('button', name='Punch in', exact=True).click()
        await expect(attendance).to_contain_text('已打卡')
        await fits(page, f'staff-punched-{width}.png')
        with closing(fixture.connect()) as con:
            assert con.execute('SELECT COUNT(*) FROM schedule_attendance').fetchone()[0] == 1
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
                    for width in (390, 1440):
                        await run(browser, width)
                finally:
                    await browser.close()
        finally:
            process.terminate(); process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
    print('PASS: punch-in places — the admin saves DT standing in it (a rough fix refused) and MK from a map pin, then '
          'clears MK; staff 1.1 km away are told how far, at the store the punch counts; at 390 and 1440 px')
