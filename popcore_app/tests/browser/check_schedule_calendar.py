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
        con.execute('''INSERT INTO shifts(employee_id,store_id,date,start_time,end_time,assigned_by)
            VALUES (103,(SELECT id FROM stores WHERE code='MK'),?,'12:00','21:00','fixture')''', (str(START + timedelta(days=5)),))
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
    await expect(page.get_by_role('button', name='Edit reminder', exact=True)).to_have_count(0)
    await expect(page.get_by_text('提前十分钟到！', exact=True)).to_be_visible()
    await expect(page.locator('.pc-personal-shift').filter(has_text='Full day').first).to_be_visible()
    await expect(page.locator('.pc-personal-shift').filter(has_text='Half day (AM)').first).to_be_visible()
    await page.screenshot(path=OUT/'my-shifts-390.png', full_page=True, animations='disabled')
    await context.close()

    context, page = await page_for('manager')
    await expect(page.get_by_role('button', name='Expand navigation', exact=True)).to_be_visible()
    await page.get_by_role('button', name='Expand navigation', exact=True).click()
    await expect(page.get_by_role('button', name='Collapse navigation', exact=True)).to_be_visible()
    await page.get_by_role('link', name='Today', exact=True).click()
    await expect(page.get_by_role('heading', name='Today / 今日', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Collapse navigation', exact=True)).to_be_visible()
    await page.get_by_role('link', name='Schedule', exact=True).click()
    await expect(page.get_by_role('button', name='Expand navigation', exact=True)).to_be_visible()
    await page.get_by_role('tab', name='My shifts', exact=True).click()
    await page.get_by_role('button', name='Edit reminder', exact=True).click()
    await page.get_by_label('Reminder for all employees (both stores)', exact=True).fill('提前十分钟到！\nPlease check your shift time.')
    await page.route('**/api/schedule/reminder', lambda route: route.fulfill(status=500, content_type='application/json', body='{}'))
    await page.get_by_role('button', name='Save reminder', exact=True).click()
    await expect(page.get_by_text('Could not save the reminder. Your edits are still here; retry saving.', exact=True)).to_be_visible()
    await expect(page.get_by_label('Reminder for all employees (both stores)', exact=True)).to_have_value('提前十分钟到！\nPlease check your shift time.')
    await page.set_viewport_size({'width':390,'height':980})
    assert await page.evaluate('document.body.scrollWidth') <= 392
    await page.wait_for_function("[...document.querySelectorAll('.ant-tabs-tabpane-active .fc table')].every(table => table.getBoundingClientRect().width < 390)")
    await page.screenshot(path=OUT/'reminder-editor-390.png', full_page=True, animations='disabled')
    await page.set_viewport_size({'width':1440,'height':980})
    await page.unroute('**/api/schedule/reminder')
    await page.get_by_role('button', name='Save reminder', exact=True).click()
    await expect(page.get_by_text('提前十分钟到！\nPlease check your shift time.', exact=True)).to_be_visible()
    await page.reload()
    await page.get_by_role('tab', name='My shifts', exact=True).click()
    await expect(page.get_by_text('提前十分钟到！\nPlease check your shift time.', exact=True)).to_be_visible()
    await page.get_by_role('button', name='Edit reminder', exact=True).click()
    await page.get_by_label('Reminder for all employees (both stores)', exact=True).fill('Discard this')
    await page.get_by_role('button', name='Cancel', exact=True).click()
    await expect(page.get_by_text('Discard this', exact=True)).to_have_count(0)
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
    workspace = page.locator('.pc-assignment-workspace')
    # Both stores must fit without moving the page.
    await page.evaluate('window.scrollTo(0, 0)')
    calendars = page.locator('.pc-store-calendars > section')
    await expect(calendars).to_have_count(2)
    first, second, side = await calendars.nth(0).bounding_box(), await calendars.nth(1).bounding_box(), await panel.bounding_box()
    assert second['y'] + second['height'] <= 980, (first, second, side)
    assert abs(first['y'] - second['y']) < 2 and second['x'] >= first['x'] + first['width'], (first, second)
    assert side['x'] >= second['x'] + second['width'], (second, side)
    await page.screenshot(path=OUT/'assignment-workspace-1440.png', full_page=True, animations='disabled')
    await page.get_by_role('button', name=f'View {START} MK', exact=True).click()
    await expect(page.get_by_role('region', name=f'Schedule details {START} MK')).to_be_visible()
    await expect(page.locator('.pc-schedule-detail')).to_have_count(1)
    await page.get_by_role('button', name=f'View {START} DT', exact=True).click()
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
    await expect(calendars.nth(1).locator('.pc-shift').filter(has_text='Mason')).to_be_visible()
    await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
    controls = await page.locator('.pc-assignment-store-switch').bounding_box()
    assert controls['y'] >= 64, controls

    for width in (1440,768,390):
        await page.set_viewport_size({'width':width,'height':980})
        await expect(page.get_by_role('heading', name='Schedule', exact=True)).to_be_visible()
        if width == 390:
            await expect(page.locator('.pc-mobile-shift-kind').filter(has_text='Full day').first).to_be_visible()
            await expect(page.locator('.pc-mobile-shift-kind').filter(has_text='Half day (AM)').first).to_be_visible()
        assert await page.evaluate('document.body.scrollWidth') <= width + 2
        await page.screenshot(path=OUT / f'manager-{width}.png', full_page=True, animations='disabled')
    await page.get_by_role('button', name=f'View {START + timedelta(days=1)} MK', exact=True).click()
    mobile_panel = await page.locator('.pc-assignment-sidebar').bounding_box()
    assert 64 <= mobile_panel['y'] < 200, mobile_panel
    await expect(page.get_by_role('region', name=f'Schedule details {START + timedelta(days=1)} MK')).to_be_visible()
    # Re-selecting the same date must also bring its controls back into view.
    await page.get_by_role('button', name=f'View {START + timedelta(days=1)} MK', exact=True).click()
    await page.wait_for_function("document.querySelector('.pc-assignment-sidebar').getBoundingClientRect().top < 200")
    await page.evaluate("document.documentElement.style.zoom='2'")
    assert await page.evaluate('document.body.scrollWidth') <= 392
    await page.evaluate("document.documentElement.style.zoom='1'")
    await page.get_by_role('button', name='Day', exact=True).click()
    await expect(page.locator('.pc-mobile-grid-kind').filter(has_text='Full day').first).to_be_visible()
    await expect(page.locator('.pc-mobile-grid-kind').filter(has_text='Half day (AM)').first).to_be_visible()
    # A busy month must show every employee in both stores without overflow counts.
    with closing(case.connect()) as con:
        for employee_id in (*range(201,207), *range(301,307)):
            con.execute("INSERT INTO employees(id,auth0_id,name) VALUES (?,?,?)", (employee_id, f'fixture|{employee_id}', f'Staff {employee_id}'))
        for day in range(-31,31):
            date_str = str(START.replace(day=1) + timedelta(days=day))
            for code, employee_ids in (('DT', range(201,207)), ('MK', range(301,307))):
                for employee_id in employee_ids:
                    con.execute("INSERT OR IGNORE INTO shifts(employee_id,store_id,date,start_time,end_time,assigned_by) VALUES (?,(SELECT id FROM stores WHERE code=?),?,'12:00','17:00','fixture')", (employee_id, code, date_str))
        con.commit()
    await page.set_viewport_size({'width':1440,'height':900})
    await page.get_by_role('button', name='Month', exact=True).click()
    await page.get_by_role('button', name='Refresh', exact=True).click()
    for width, height in ((1440,900), (1280,800), (1366,768), (1920,1080)):
        await page.set_viewport_size({'width':width,'height':height})
        await page.wait_for_function("[...document.querySelectorAll('.pc-store-calendars .fc table')].every(table => table.getBoundingClientRect().width <= table.closest('.fc').getBoundingClientRect().width + 1)")
        await expect(page.locator('.pc-store-calendars .fc-daygrid-more-link')).to_have_count(0)
        for calendar in (calendars.nth(0), calendars.nth(1)):
            await expect(calendar.locator('.fc-daygrid-day-events .pc-shift:visible').first).to_be_visible()
        first, second = await calendars.nth(0).bounding_box(), await calendars.nth(1).bounding_box()
        assert abs(first['y'] - second['y']) < 2, (first,second)
        for code in ('DT','MK'):
            day = page.locator(f'.pc-store-calendars section:has(button[aria-label="View {START} {code}"]) td[data-date="{START}"]')
            assert await day.locator('.pc-shift:visible').count() >= 6
        assert await page.locator('.pc-store-calendars .fc-scroller').evaluate_all('(els) => els.every(e => e.scrollHeight <= e.clientHeight + 2)'), 'Month dates must not need internal scrolling'
        await page.screenshot(path=OUT/f'month-fit-{width}.png', animations='disabled')
        await page.get_by_role('button', name=f'View {START} DT', exact=True).click()
        await page.wait_for_function("[...document.querySelectorAll('.pc-store-calendars .fc table')].every(table => table.getBoundingClientRect().width <= table.closest('.fc').getBoundingClientRect().width + 1)")
        assert await page.locator('.pc-store-calendars .fc-scroller').evaluate_all('(els) => els.every(e => e.scrollHeight <= e.clientHeight + 2)')
        for calendar in (calendars.nth(0), calendars.nth(1)):
            await expect(calendar.locator('.fc-daygrid-day-events .pc-shift:visible').first).to_be_visible()
        await page.screenshot(path=OUT/f'month-assignment-fit-{width}.png', animations='disabled')
        await page.get_by_role('button', name='Close day details', exact=True).click()
    # Representative monthly staffing: four daily at Downtown, two at Markham, plus existing shifts.
    with closing(case.connect()) as con:
        con.execute('DELETE FROM shifts WHERE employee_id IN (205,206,303,304,305,306)')
        con.execute("UPDATE employees SET name=CASE id WHEN 201 THEN 'Cindy' WHEN 202 THEN 'FULIN' WHEN 203 THEN 'Xiao Chen' WHEN 204 THEN 'Zimo' WHEN 301 THEN 'LONG' WHEN 302 THEN 'Xiao Nan' ELSE name END")
        con.execute("UPDATE employees SET color=CASE id WHEN 201 THEN '#6DADE0' WHEN 202 THEN '#C4EC73' WHEN 203 THEN '#E6A06B' WHEN 204 THEN '#DC28C6' WHEN 301 THEN '#397F22' WHEN 302 THEN '#13CCCC' ELSE color END")
        con.commit()
    await page.set_viewport_size({'width':1366,'height':800})
    await page.reload()
    await page.get_by_role('button', name='Month', exact=True).click()
    await page.get_by_role('button', name=f'View {START} DT', exact=True).click()
    await expect(page.get_by_role('region', name=f'Schedule details {START} DT').get_by_text('Assigned · 6', exact=True)).to_be_visible()
    await expect(page.locator('.pc-store-calendars .fc-daygrid-more-link')).to_have_count(0)
    await page.wait_for_function("[...document.querySelectorAll('.pc-store-calendar-body')].every(el => el.getBoundingClientRect().bottom <= innerHeight)", timeout=5000)
    assert await page.locator('.pc-content').evaluate('(el) => el.scrollHeight <= el.clientHeight + 2')
    assert await page.locator('.pc-store-calendars .fc-scroller').evaluate_all('(els) => els.every(e => e.scrollHeight <= e.clientHeight + 2)')
    await page.screenshot(path=OUT/'all-shifts-MONTH-1366.png', animations='disabled')
    await page.get_by_role('button', name='Month', exact=True).click()
    # Check the previous month too (August 2026 has six actual calendar weeks).
    await page.set_viewport_size({'width':1280,'height':800})
    await page.get_by_role('button', name='Previous calendar period', exact=True).click()
    previous_date = START.replace(day=1) - timedelta(days=1)
    await page.get_by_role('button', name=f'View {previous_date} MK', exact=True).click()
    await expect(page.get_by_role('region', name=f'Schedule details {previous_date} MK')).to_be_visible()
    await page.wait_for_function("[...document.querySelectorAll('.pc-store-calendar-body')].every(el => el.getBoundingClientRect().bottom <= innerHeight)", timeout=5000)
    assert await page.locator('.pc-store-calendars .fc-scroller').evaluate_all('(els) => els.every(e => e.scrollHeight <= e.clientHeight + 2)')
    await page.screenshot(path=OUT/'previous-month-fit-1280.png', animations='disabled')
    await page.set_viewport_size({'width':1024,'height':800})
    await expect(page.locator('.pc-store-calendars .fc-daygrid-more-link')).to_have_count(0)
    await page.wait_for_function("[...document.querySelectorAll('.pc-store-calendar-body')].every(el => el.scrollHeight <= el.clientHeight + 2)")
    assert await page.locator('.pc-store-calendars .fc-scroller').evaluate_all('(els) => els.every(e => e.scrollHeight <= e.clientHeight + 2)')
    await context.close()
    context, page = await page_for('staff', 390)
    await page.get_by_role('tab', name='My shifts', exact=True).click()
    await expect(page.get_by_text('提前十分钟到！\nPlease check your shift time.', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Edit reminder', exact=True)).to_have_count(0)
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
