"""Schedule UI against real Flask APIs and a disposable SQLite database; no Auth0/network."""
import asyncio
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect
from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server

sys.path.insert(0, str(HERE.parent))
from support import IsolatedApiCase
from blueprints import users

OUT = HERE.parents[2] / '.local' / 'schedule-browser'
ANCHOR = date(2026, 9, 14)
START = date.today() - timedelta(days=(date.today() - ANCHOR).days % 14)


async def run_checks(browser, case):
    case.app.register_blueprint(users.bp)
    with closing(case.connect()) as con:
        con.executescript("""
            INSERT INTO employees(id,auth0_id,name,email) VALUES
              (101,'auth0|staff','Jessi','jessi@test.invalid'),
              (102,'auth0|celia','Celia','celia@test.invalid'),
              (103,'auth0|mason','Mason','mason@test.invalid');
            UPDATE employees SET color='#145667' WHERE id=102;
            UPDATE employees SET color='#ffe59a' WHERE id=101;
            INSERT INTO employee_stores(employee_id,store_id) VALUES (101,1),(102,1),(103,1);
            INSERT INTO app_settings(key,value) VALUES ('schedule_shift_presets','[{"label":"2-7","start":"15:00","end":"19:00"}]');
        """)
        for offset, end in ((2, '22:00'), (3, '17:00')):
            con.execute('''INSERT INTO shifts(employee_id,store_id,date,start_time,end_time,assigned_by)
                VALUES (101,1,?,'12:00',?,'fixture')''', (str(START + timedelta(days=offset)), end))
        con.commit()
    days = [{'date': str(START + timedelta(days=i)), 'status': 'available', 'start_time': '12:00', 'end_time': '17:00', 'notes': ''} for i in range(14)]
    days[1].update(status='unavailable', start_time='', end_time='', notes='Class all day')
    result = case.client.put('/api/schedule/availability/period', headers=case.headers('staff:celia'), json={
        'period_start': str(START), 'store_code': 'DT', 'version': 0, 'days': days,
    })
    assert result.status_code == 200, result.get_json()

    async def page_for(role, width=1440):
        state = {'mode': 'data'}
        context, page = await context_with_api(browser, state, viewport={'width': width, 'height': 980}, auth={'role': role})
        await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown Toronto',color:'#3b82f6'}))")
        async def api(route):
            parsed = urlparse(route.request.url)
            path = parsed.path + ('?' + parsed.query if parsed.query else '')
            body = route.request.post_data_json if route.request.post_data else None
            response = case.client.open(path, method=route.request.method, headers=case.headers(role), json=body)
            await route.fulfill(status=response.status_code, content_type='application/json', body=json.dumps(response.get_json()))
        await page.route('**/api/schedule/**', api)
        await page.route('**/api/employees/stores', api)
        await page.route(BASE + '/api/today*', api)
        page.on('pageerror', lambda error: print('Browser error:', error))
        page.on('console', lambda message: print('Console:', message.text) if message.type == 'error' else None)
        await page.goto(BASE + '/schedule')
        return context, page

    context, page = await page_for('staff', 390)
    await expect(page.get_by_role('heading', name='Schedule', exact=True)).to_be_visible()
    await expect(page.get_by_label('Availability employee', exact=True)).to_have_count(0)
    calendar = page.get_by_label('Two-week availability calendar')
    await expect(calendar.locator('button')).to_have_count(14)
    await expect(page.get_by_role('button', name='Submit two weeks', exact=True)).to_be_disabled()
    # Keyboard and multi-date entry; seven days available, repeat to complete the cycle.
    for i in range(7):
        await calendar.locator('button').nth(i).click()
    await page.get_by_label('Available from', exact=True).fill('12:00')
    await page.get_by_label('Available until', exact=True).fill('22:00')
    await page.get_by_role('button', name='Apply to selected dates').click()
    await page.get_by_role('button', name='Repeat first week').click()
    await page.get_by_role('button', name='Submit two weeks', exact=True).click()
    await expect(page.get_by_role('status').filter(has_text='Submitted')).to_be_visible()
    assert case.client.get(f'/api/schedule/availability/period?period_start={START}&store_code=DT', headers=case.headers('staff')).get_json()['version'] == 1
    assert await page.evaluate('document.body.scrollWidth') <= 392
    await page.screenshot(path=OUT / 'employee-390.png', full_page=True, animations='disabled')
    # Draft survives switching stores; stale submission keeps edits until an explicit reload.
    await calendar.locator('button').nth(0).click()
    await page.get_by_label('Available until', exact=True).fill('18:00')
    await page.get_by_role('button', name='Apply to selected dates').click()
    await page.get_by_role('link', name='Today', exact=True).click()
    await expect(page.get_by_text('Today / 今日', exact=True)).to_be_visible()
    assert await page.evaluate("""() => {
        const event = new Event('beforeunload', {cancelable: true});
        window.dispatchEvent(event); return event.defaultPrevented;
    }"""), 'Leaving the app must warn about a draft even outside Schedule'
    await page.get_by_role('link', name='Schedule', exact=True).click()
    await expect(calendar.locator('button').nth(0)).to_contain_text('18:00')
    await page.get_by_label('Operations store').select_option('MK')
    await expect(page.get_by_role('button', name='Submit two weeks', exact=True)).to_be_disabled()
    await page.get_by_label('Operations store').select_option('DT')
    await expect(calendar.locator('button').nth(0)).to_contain_text('18:00')
    case.client.post('/api/schedule/availability', headers=case.headers('staff'), json={'date':str(START),'store_code':'DT','start_time':'12:00','end_time':'20:00'})
    await page.get_by_role('button', name='Resubmit two weeks').click()
    await expect(page.get_by_text('Availability changed. Reload this period before resubmitting.', exact=True)).to_be_visible()
    await expect(calendar.locator('button').nth(0)).to_contain_text('18:00')
    await page.get_by_role('button', name='Discard edits and reload').click()
    await expect(calendar.locator('button').nth(0)).to_contain_text('20:00')
    await page.get_by_role('button', name='Submit two weeks', exact=True).click()
    await expect(page.get_by_role('status').filter(has_text='Submitted')).to_be_visible()
    await page.get_by_role('tab', name='My shifts', exact=True).click()
    await expect(page.locator('.pc-personal-shift').filter(has_text='Full day').first).to_be_visible()
    await expect(page.locator('.pc-personal-shift').filter(has_text='Half day (AM)').first).to_be_visible()
    await page.screenshot(path=OUT/'my-shifts-390.png', full_page=True, animations='disabled')
    await context.close()

    context, page = await page_for('manager')
    await page.get_by_role('tab', name='Availability', exact=True).click()
    switcher = page.get_by_label('Availability employee', exact=True).first
    await switcher.click()
    await page.get_by_title('Celia', exact=True).click()
    review = page.get_by_role('region', name='Declared availability')
    await expect(review.get_by_role('status')).to_contain_text('Submitted')
    await expect(review.locator('.pc-availability-day')).to_have_count(14)
    await expect(review.locator('.pc-availability-day').nth(0)).to_contain_text('12:00–17:00')
    await expect(review.locator('.pc-availability-day').nth(1)).to_contain_text('Unavailable')
    await expect(review.locator('.pc-availability-day').nth(1)).to_contain_text('Class all day')
    await expect(review.get_by_role('button', name='Submit two weeks', exact=True)).to_have_count(0)
    await switcher.click()
    await page.get_by_title('Mason', exact=True).click()
    await expect(review.get_by_role('status')).to_contain_text('Not submitted')
    await expect(review.locator('.pc-availability-day').nth(0)).to_contain_text('Not submitted')
    await switcher.click()
    await page.get_by_title('Jessi', exact=True).click()
    await expect(review.locator('.pc-availability-day').nth(0)).to_contain_text('12:00–20:00')
    await page.get_by_label('Operations store').select_option('MK')
    await expect(review.get_by_role('status')).to_contain_text('Not submitted')
    await page.get_by_label('Operations store').select_option('DT')
    await expect(review.get_by_role('status')).to_contain_text('Submitted')
    await page.get_by_role('button', name='Next declared availability period').click()
    await expect(review.get_by_role('status')).to_contain_text('Not submitted')
    await page.get_by_role('button', name='Previous declared availability period').click()
    await expect(review.locator('.pc-availability-day').nth(0)).to_contain_text('12:00–20:00')
    await page.set_viewport_size({'width':390,'height':980})
    assert await page.evaluate('document.body.scrollWidth') <= 392
    await page.screenshot(path=OUT/'employee-switcher-390.png', full_page=True, animations='disabled')
    await page.set_viewport_size({'width':1440,'height':980})
    await page.get_by_role('tab', name='Team schedule', exact=True).click()
    await expect(page.get_by_role('button', name='Two weeks', exact=True)).to_have_attribute('aria-pressed', 'true')
    await page.get_by_role('button', name='Availability', exact=True).click()
    await expect(page.locator('.fc-event').filter(has_text='Celia').first).to_be_visible()
    # Date selection opens details, not an immediate assignment modal.
    await page.get_by_role('button', name=f'View {START} DT', exact=True).focus()
    await page.keyboard.press('Enter')
    panel = page.get_by_role('region', name=f'Schedule details {START} DT')
    await expect(panel.get_by_text('Not submitted', exact=True).first).to_be_visible()
    celia = panel.locator('.pc-schedule-person').filter(has_text='Celia')
    await celia.get_by_role('button', name='Assign', exact=True).click()
    dialog = page.get_by_role('dialog')
    await expect(dialog.get_by_text('Shift type', exact=True)).to_be_visible()
    await dialog.get_by_text('Full day (12:00–22:00)', exact=True).click()
    await expect(dialog.get_by_role('button', name='Save', exact=True)).to_be_disabled()
    await expect(dialog.get_by_role('alert')).to_contain_text('outside availability')
    await dialog.get_by_text('2-7 (15:00–19:00)', exact=True).click()
    await expect(dialog.get_by_role('button', name='Save', exact=True)).to_be_disabled()
    await dialog.get_by_text('Half (12:00–17:00)', exact=True).click()
    await expect(dialog.get_by_role('button', name='Save', exact=True)).to_be_enabled()
    await page.screenshot(path=OUT / 'shift-types-1440.png', full_page=True, animations='disabled')
    await page.set_viewport_size({'width':390,'height':680})
    await dialog.get_by_role('button', name='Save', exact=True).scroll_into_view_if_needed()
    bounds=await dialog.bounding_box()
    assert bounds['y']>=0 and bounds['y']+bounds['height']<=680
    await page.screenshot(path=OUT/'shift-types-390.png',full_page=True, animations='disabled')
    await page.set_viewport_size({'width':1440,'height':980})
    await dialog.get_by_role('button', name='Save', exact=True).click()
    await expect(dialog).not_to_be_visible()
    with closing(case.connect()) as con:
        saved = con.execute('SELECT * FROM shifts WHERE employee_id=102 AND date=?', (str(START),)).fetchone()
        assert saved and saved['start_time']=='12:00' and saved['end_time']=='17:00'
    await expect(panel.get_by_text('Assigned · 1', exact=True)).to_be_visible()
    await panel.locator('.pc-schedule-person').filter(has_text='Mason').get_by_role('button', name='Assign', exact=True).click()
    await expect(dialog.get_by_role('alert')).to_contain_text('You can still assign this shift')
    await dialog.get_by_text('Full day (12:00–22:00)', exact=True).click()
    await expect(dialog.get_by_role('button', name='Save', exact=True)).to_be_enabled()
    await dialog.get_by_role('button', name='Save', exact=True).click()
    await expect(dialog).not_to_be_visible()
    await expect(panel.get_by_text('Assigned · 2', exact=True)).to_be_visible()
    with closing(case.connect()) as con:
        assert con.execute('SELECT end_time FROM shifts WHERE employee_id=103 AND date=?', (str(START),)).fetchone()[0] == '22:00'
    await page.get_by_role('button', name='Assigned shifts', exact=True).click()
    for width in (1440,768,390):
        await page.set_viewport_size({'width':width,'height':980})
        await expect(page.get_by_role('heading', name='Schedule', exact=True)).to_be_visible()
        if width == 390:
            await expect(page.locator('.pc-mobile-shift-kind').filter(has_text='Full day').first).to_be_visible()
            await expect(page.locator('.pc-mobile-shift-kind').filter(has_text='Half day (AM)').first).to_be_visible()
        assert await page.evaluate('document.body.scrollWidth') <= width + 2
        await page.screenshot(path=OUT / f'manager-{width}.png', full_page=True, animations='disabled')
    await page.evaluate("document.documentElement.style.zoom='2'")
    assert await page.evaluate('document.body.scrollWidth') <= 392
    await page.evaluate("document.documentElement.style.zoom='1'")
    await page.get_by_role('button', name='Day', exact=True).click()
    await expect(page.locator('.pc-mobile-grid-kind').filter(has_text='Full day').first).to_be_visible()
    await expect(page.locator('.pc-mobile-grid-kind').filter(has_text='Half day (AM)').first).to_be_visible()
    await context.close()


async def main():
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',5174)) == 0:
            raise RuntimeError('Port 5174 is already in use')
    OUT.mkdir(parents=True, exist_ok=True)
    case = IsolatedApiCase(); case.setUp()
    with (OUT/'vite.log').open('w') as log:
        process = subprocess.Popen(['node', str(FRONTEND/'node_modules/vite/bin/vite.js'), '--config', str(HERE/'vite.config.mjs')], cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            await wait_for_server(process)
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    await run_checks(browser, case)
                except Exception:
                    for context in browser.contexts:
                        if context.pages:
                            await context.pages[-1].screenshot(path=OUT/'failure.png', full_page=True, animations='disabled')
                            break
                    raise
                finally: await browser.close()
        finally:
            process.terminate(); process.wait(timeout=10); case.tearDown()

if __name__=='__main__':
    started=time.perf_counter(); asyncio.run(main())
    print(f'Schedule calendar real-API browser checks passed in {time.perf_counter()-started:.1f}s')
