"""Schedule UI against real Flask APIs and a disposable SQLite database; no Auth0/network."""
import asyncio
from contextlib import closing
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

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
        con.execute('''INSERT INTO shifts(employee_id,store_id,date,start_time,end_time,assigned_by,position)
            VALUES (103,(SELECT id FROM stores WHERE code='MK'),?,'12:00','21:00','fixture','Cashier')''', (str(START + timedelta(days=5)),))
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
    await expect(page.get_by_role('heading', name='排班', exact=True)).to_be_visible()
    await expect(page.get_by_label('Availability employee', exact=True)).to_have_count(0)
    calendar = page.get_by_label('Two-week availability calendar')
    await expect(calendar.locator('button')).to_have_count(14)
    await expect(page.get_by_role('button', name='Submit two weeks', exact=True)).to_be_disabled()
    # Keyboard and multi-date entry; seven days available, repeat to complete the cycle.
    for i in range(7):
        await calendar.locator('button').nth(i).click()
    # Full and half days follow each date's hours: weekend halves split at 16:30.
    await expect(page.get_by_label('Available from', exact=True)).to_have_count(0)
    await page.get_by_text('Half day (PM)', exact=True).click()
    await expect(page.get_by_text('Mon–Fri 17:00–22:00 · Sat–Sun 16:30–22:00', exact=True)).to_be_visible()
    await page.get_by_role('button', name='Apply to selected dates').click()
    await expect(calendar.locator('button').nth(4)).to_contain_text('17:00–22:00')
    await expect(calendar.locator('button').nth(5)).to_contain_text('16:30–22:00')
    for i in range(7):
        await calendar.locator('button').nth(i).click()
    await page.get_by_text('Full day', exact=True).click()
    await page.get_by_role('button', name='Apply to selected dates').click()
    await expect(calendar.locator('button').nth(0)).to_contain_text('12:00–22:00')
    await expect(calendar.locator('button').nth(6)).to_contain_text('11:00–22:00')
    await page.get_by_role('button', name='Repeat first week').click()
    await page.get_by_role('button', name='Submit two weeks', exact=True).click()
    await expect(page.get_by_role('status').filter(has_text='Submitted')).to_be_visible()
    assert case.client.get(f'/api/schedule/availability/period?period_start={START}&store_code=DT', headers=case.headers('staff')).get_json()['version'] == 1
    assert await page.evaluate('document.body.scrollWidth') <= 392
    await page.screenshot(path=OUT / 'employee-390.png', full_page=True, animations='disabled')
    # Draft survives switching stores; stale submission keeps edits until an explicit reload.
    await calendar.locator('button').nth(0).click()
    await page.get_by_text('Custom hours', exact=True).click()
    await page.get_by_label('Available until', exact=True).fill('18:00')
    await page.get_by_role('button', name='Apply to selected dates').click()
    await page.get_by_role('link', name='首页 Home', exact=True).click()
    await page.get_by_role('link', name='待处理', exact=False).first.click()
    await expect(page.get_by_text('Today / 今日', exact=True)).to_be_visible()
    assert await page.evaluate("""() => {
        const event = new Event('beforeunload', {cancelable: true});
        window.dispatchEvent(event); return event.defaultPrevented;
    }"""), 'Leaving the app must warn about a draft even outside Schedule'
    await page.get_by_role('link', name='排班 Schedule', exact=True).click()
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
    await page.get_by_role('tab', name='我的班 My shifts', exact=True).click()
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
    # Store tasks left the menus (approved redesign); leave Schedule through Home instead.
    await page.get_by_role('link', name='首页 Home', exact=True).click()
    await expect(page.get_by_role('button', name='Collapse navigation', exact=True)).to_be_visible()
    await page.get_by_role('link', name='排班 Schedule', exact=True).click()
    await expect(page.get_by_role('button', name='Expand navigation', exact=True)).to_be_visible()
    await page.get_by_role('tab', name='我的班 My shifts', exact=True).click()
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
    await page.get_by_role('tab', name='我的班 My shifts', exact=True).click()
    await expect(page.get_by_text('提前十分钟到！\nPlease check your shift time.', exact=True)).to_be_visible()
    await page.get_by_role('button', name='Edit reminder', exact=True).click()
    await page.get_by_label('Reminder for all employees (both stores)', exact=True).fill('Discard this')
    await page.get_by_role('button', name='Cancel', exact=True).click()
    await expect(page.get_by_text('Discard this', exact=True)).to_have_count(0)
    await page.get_by_role('tab', name='可上时间 Availability', exact=True).click()
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
    await page.get_by_role('tab', name='团队排班 Team schedule', exact=True).click()
    await expect(page.get_by_role('button', name='Two weeks', exact=True)).to_have_attribute('aria-pressed', 'true')
    await page.get_by_role('button', name='Availability', exact=True).click()
    await expect(page.locator('.fc-event').filter(has_text='Celia').first).to_be_visible()
    # The checklist floats as an icon: click opens or collapses it, dragging moves it without toggling.
    checklist = page.get_by_role('list', name='Employee coverage checklist')
    checklist_toggle = page.locator('.pc-coverage-toggle')
    async def show_checklist(shown=True):
        if (await checklist_toggle.get_attribute('aria-expanded') == 'true') != shown:
            await checklist_toggle.click()
        await expect(checklist).to_be_visible() if shown else await expect(checklist).to_be_hidden()
    await expect(checklist).to_be_hidden()
    await expect(checklist_toggle).to_have_text('0/4')
    resting = await checklist_toggle.bounding_box()
    assert resting['x'] + resting['width'] > 1440 - 24 and resting['y'] > 700, resting
    await page.mouse.move(resting['x'] + 20, resting['y'] + 20)
    await page.mouse.down()
    await page.mouse.move(700, 400, steps=5)
    await page.mouse.up()
    moved = await checklist_toggle.bounding_box()
    assert abs(moved['x'] + 20 - 700) < 3 and abs(moved['y'] + 20 - 400) < 3, moved
    await expect(checklist).to_be_hidden()
    await show_checklist()
    await page.reload()
    await page.get_by_role('button', name='Availability', exact=True).click()
    await expect(checklist).to_be_visible()
    assert abs((await checklist_toggle.bounding_box())['x'] - moved['x']) < 3
    await expect(checklist.get_by_role('checkbox', name='Unchecked: Celia', exact=True)).not_to_be_checked()
    await checklist.get_by_role('checkbox', name='Unchecked: Jessi', exact=True).check()
    await expect(checklist.get_by_role('checkbox', name='Checked: Jessi', exact=True)).to_be_checked()
    assert await checklist.locator('.pc-employee-name').filter(has_text='Celia').evaluate('(el) => getComputedStyle(el).backgroundColor') == 'rgb(20, 86, 103)'
    await expect(checklist_toggle).to_contain_text('1/4 reviewed')
    await page.screenshot(path=OUT/'checklist-open-1440.png', animations='disabled')
    await show_checklist(False)
    await expect(checklist_toggle).to_have_text('1/4')
    # Date selection opens assignment immediately, with submitted availability first.
    await page.get_by_role('button', name=f'View {START} DT', exact=True).focus()
    await page.keyboard.press('Enter')
    dialog = page.get_by_role('dialog')
    await expect(dialog.get_by_text('Assign shift', exact=False)).to_be_visible()
    await expect(dialog.get_by_text(f'Available on {START}:')).to_be_visible()
    await dialog.get_by_role('combobox', name='Employee').click()
    await expect(dialog.locator('.ant-select-item-option-content').first).to_contain_text('Celia')
    await dialog.get_by_role('combobox', name='Employee').click()
    await dialog.get_by_role('button', name='Cancel', exact=True).click()
    async def select_date(date, code):
        await page.get_by_role('button', name=f'View {date} {code}', exact=True).click()
        await expect(dialog).to_be_visible()
        await dialog.get_by_role('button', name='Cancel', exact=True).click()

    panel = page.get_by_role('region', name=f'Schedule details {START} DT')
    await expect(panel.get_by_text('Not submitted', exact=True).first).to_be_visible()
    workspace = page.locator('.pc-assignment-workspace')
    # Locations stack and use the calendar workspace's full width.
    await page.evaluate('window.scrollTo(0, 0)')
    calendars = page.locator('.pc-store-calendars > section')
    await expect(calendars).to_have_count(2)
    first, second, side = await calendars.nth(0).bounding_box(), await calendars.nth(1).bounding_box(), await panel.bounding_box()
    assert second['y'] >= first['y'] + first['height'], (first, second)
    assert abs(first['x'] - second['x']) < 2 and abs(first['width'] - second['width']) < 2, (first, second)
    assert first['width'] == await page.locator('.pc-store-calendars').evaluate('(el) => el.getBoundingClientRect().width')
    assert side['y'] >= second['y'] + second['height'] and abs(side['width'] - first['width']) < 40, (second, side)
    # Bars show their length: a morning half fills the left half of its day, a full day the whole cell.
    async def bar(code, date, name):
        return await page.locator(f'.pc-store-calendars section:has(button[aria-label="View {date} {code}"]) td[data-date="{date}"] .pc-shift-bar').filter(has_text=name).evaluate(
            '(el) => { const b = el.getBoundingClientRect(), d = el.closest(".fc-daygrid-day-events").getBoundingClientRect(); return [(b.left - d.left) / d.width, b.width / d.width] }')
    left, width = await bar('DT', START, 'Celia')
    assert left < 0.03 and 0.45 < width < 0.55, (left, width)
    await page.screenshot(path=OUT/'assignment-workspace-1440.png', full_page=True, animations='disabled')
    await select_date(START, 'MK')
    await expect(page.get_by_role('region', name=f'Schedule details {START} MK')).to_be_visible()
    await expect(page.locator('.pc-schedule-detail')).to_have_count(1)
    await select_date(START, 'DT')
    celia = panel.locator('.pc-schedule-person').filter(has_text='Celia')
    await celia.get_by_role('button', name='Assign', exact=True).click()
    dialog = page.get_by_role('dialog')
    await expect(dialog.get_by_text('Shift type', exact=True)).to_be_visible()
    await dialog.get_by_text('Full day (12:00–22:00)', exact=True).click()
    await expect(dialog.get_by_role('button', name='Save', exact=True)).to_be_enabled()
    await expect(dialog.get_by_role('alert')).to_contain_text('outside availability (12:00–17:00). You can still assign this shift')
    await dialog.get_by_text('2-7 (15:00–19:00)', exact=True).click()
    await dialog.get_by_role('button', name='Save', exact=True).click()
    await expect(dialog.get_by_text('Assign outside submitted availability', exact=True)).to_be_visible()
    await expect(dialog.get_by_role('alert')).to_contain_text('Celia submitted 12:00–17:00')
    with closing(case.connect()) as con:
        assert con.execute('SELECT id FROM shifts WHERE employee_id=102 AND date=?', (str(START),)).fetchone() is None
    await dialog.get_by_role('button', name='Go Back', exact=True).click()
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
    await expect(celia.locator('.pc-assignment-kind')).to_have_text('Half day (AM)')
    assert await celia.locator('.pc-employee-name').evaluate('(el) => getComputedStyle(el).backgroundColor') == 'rgb(20, 86, 103)'
    await show_checklist()
    await expect(checklist.get_by_role('checkbox', name='Unchecked: Celia', exact=True)).not_to_be_checked()
    await show_checklist(False)
    await panel.locator('.pc-schedule-person').filter(has_text='Mason').get_by_role('button', name='Assign', exact=True).click()
    await expect(dialog.get_by_role('alert')).to_contain_text('You can still assign this shift')
    await dialog.get_by_text('Full day (12:00–22:00)', exact=True).click()
    await expect(dialog.get_by_role('button', name='Save', exact=True)).to_be_enabled()
    await dialog.get_by_role('button', name='Save', exact=True).click()
    await expect(dialog).not_to_be_visible()
    await expect(panel.get_by_text('Assigned · 2', exact=True)).to_be_visible()
    await expect(panel.locator('.pc-schedule-person').filter(has_text='Mason').locator('.pc-assignment-kind')).to_have_text('Full day')
    await panel.locator('.pc-schedule-person').filter(has_text='Mason').get_by_role('button', name='Edit', exact=True).click()
    await dialog.get_by_text('2-7 (15:00–19:00)', exact=True).click()
    await dialog.get_by_role('button', name='Save', exact=True).click()
    await expect(dialog).not_to_be_visible()
    await expect(panel.locator('.pc-schedule-person').filter(has_text='Mason').locator('.pc-assignment-kind')).to_have_text('Custom')
    await panel.locator('.pc-schedule-person').filter(has_text='Mason').get_by_role('button', name='Edit', exact=True).click()
    await dialog.get_by_text('Full day (12:00–22:00)', exact=True).click()
    await dialog.get_by_role('button', name='Save', exact=True).click()
    await expect(dialog).not_to_be_visible()
    # Actual finish: recorded by the manager once the shift's Toronto day has arrived.
    mason = panel.locator('.pc-schedule-person').filter(has_text='Mason')
    await mason.get_by_role('button', name='Edit', exact=True).click()
    # 实际下班 is digits only with the numeric keypad: 2125 reads as 21:25; the saved value stays HH:mm.
    finish = dialog.get_by_label('实际下班 Actual finish', exact=True)
    if START <= datetime.now(ZoneInfo('America/Toronto')).date():
        await expect(finish).to_have_attribute('inputmode', 'numeric')
        await finish.fill('2125')
        await expect(dialog.get_by_text('= 21:25', exact=True)).to_be_visible()
        await dialog.get_by_role('button', name='Save', exact=True).click()
        await expect(dialog).not_to_be_visible()
        await expect(mason).to_contain_text('12:00–22:00 · finished 21:25')
        await expect(mason.locator('.pc-assignment-kind')).to_have_text('Full day')
        with closing(case.connect()) as con:
            saved = con.execute('SELECT end_time,actual_end_time FROM shifts WHERE employee_id=103 AND date=?', (str(START),)).fetchone()
            assert tuple(saved) == ('22:00', '21:25'), tuple(saved)
        await page.screenshot(path=OUT / 'actual-finish-1440.png', full_page=True, animations='disabled')
        await mason.get_by_role('button', name='Edit', exact=True).click()
        await expect(finish).to_have_value('2125')
        # A typed colon is dropped; digits that are not a time are refused and cannot be saved.
        await finish.fill('21:34')
        await expect(finish).to_have_value('2134')
        await finish.fill('2460')
        await expect(dialog.get_by_text('Type digits, e.g. 2134 for 21:34', exact=True)).to_be_visible()
        await dialog.get_by_role('button', name='Save', exact=True).click()
        await expect(dialog).to_be_visible()
        # A finish up to 01:00 belongs to the next day.
        await finish.fill('200')
        await expect(dialog.get_by_text('Must be after start, or by 01:00 the next day', exact=True)).to_be_visible()
        await finish.fill('045')
        await expect(dialog.get_by_text('= 00:45', exact=True)).to_be_visible()
        await expect(dialog.get_by_text('Counted as 00:45 the next day.', exact=True)).to_be_visible()
        await expect(dialog.get_by_text('Must be after start, or by 01:00 the next day', exact=True)).to_have_count(0)
        await expect(page.locator('.ant-message-notice')).to_have_count(0)
        await dialog.screenshot(path=OUT / 'actual-finish-dialog.png', animations='disabled')
        await dialog.get_by_role('button', name='Save', exact=True).click()
        await expect(dialog).not_to_be_visible()
        await expect(mason).to_contain_text('12:00–22:00 · finished 00:45 (next day)')
        await mason.screenshot(path=OUT / 'actual-finish-panel.png', animations='disabled')
    else:
        await expect(finish).to_have_count(0)
        await dialog.get_by_role('button', name='Cancel', exact=True).click()
        await expect(dialog).not_to_be_visible()
    await page.get_by_role('button', name='Close day details', exact=True).click()
    await show_checklist()
    await expect(checklist.get_by_role('checkbox', name='Checked: Jessi', exact=True)).to_be_checked()
    await show_checklist(False)
    await select_date(START, 'DT')
    with closing(case.connect()) as con:
        assert con.execute('SELECT end_time FROM shifts WHERE employee_id=103 AND date=?', (str(START),)).fetchone()[0] == '22:00'
    await select_date(START + timedelta(days=5), 'MK')
    await expect(page.get_by_role('region', name=f'Schedule details {START + timedelta(days=5)} MK').locator('.pc-assignment-kind')).to_have_text('Half day (PM)')
    await select_date(START, 'DT')
    await page.get_by_role('button', name='Assigned shifts', exact=True).click()
    await expect(calendars.nth(1).locator('.pc-shift').filter(has_text='Mason')).to_be_visible()
    if START <= datetime.now(ZoneInfo('America/Toronto')).date():
        # The recorded finish is marked on the calendar block itself.
        await expect(calendars.nth(0).locator('.pc-shift').filter(has_text='Mason').locator('.pc-shift-finished')).to_have_text('✓ 00:45 +1')
        await page.screenshot(path=OUT / 'actual-finish-calendar-1440.png', full_page=True, animations='disabled')
        await page.get_by_role('button', name='Week', exact=True).click()
        await expect(calendars.nth(0).locator('.pc-shift-grid').filter(has_text='Mason').locator('.pc-shift-finished')).to_have_text('✓ 00:45 +1')
        await page.screenshot(path=OUT / 'actual-finish-week-1440.png', full_page=True, animations='disabled')
        await page.get_by_role('button', name='Two weeks', exact=True).click()
        await select_date(START, 'DT')
    await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
    controls = await page.locator('.pc-assignment-store-switch').bounding_box()
    assert controls['y'] >= 64, controls

    unavailable_date = START + timedelta(days=1)
    await select_date(unavailable_date, 'DT')
    unavailable_panel = page.get_by_role('region', name=f'Schedule details {unavailable_date} DT')
    await unavailable_panel.locator('.pc-schedule-person').filter(has_text='Celia').get_by_role('button', name='Assign', exact=True).click()
    await dialog.get_by_text('Full day (12:00–22:00)', exact=True).click()
    await expect(dialog.get_by_role('alert')).to_contain_text('Employee marked this date unavailable')
    await expect(dialog.get_by_role('button', name='Save', exact=True)).to_be_enabled()
    await dialog.get_by_role('button', name='Save', exact=True).click()
    await expect(dialog.get_by_text('Assign despite unavailable date', exact=True)).to_be_visible()
    with closing(case.connect()) as con:
        assert con.execute('SELECT id FROM shifts WHERE employee_id=102 AND date=?', (str(unavailable_date),)).fetchone() is None
    await dialog.get_by_role('button', name='Go Back', exact=True).click()
    await dialog.get_by_role('button', name='Save', exact=True).click()
    await dialog.get_by_role('button', name='Assign anyway', exact=True).click()
    await expect(dialog).not_to_be_visible()
    with closing(case.connect()) as con:
        saved = con.execute('SELECT start_time,end_time FROM shifts WHERE employee_id=102 AND date=?', (str(unavailable_date),)).fetchone()
        assert saved and (saved['start_time'], saved['end_time']) == ('12:00', '22:00')

    for width in (1440,768,390):
        await page.set_viewport_size({'width':width,'height':980})
        await expect(page.get_by_role('heading', name='排班', exact=True)).to_be_visible()
        if width == 390:
            await expect(page.locator('.pc-mobile-shift-kind').filter(has_text='Full day').first).to_be_visible()
            if START <= datetime.now(ZoneInfo('America/Toronto')).date():
                await expect(page.locator('.pc-mobile-shift[title*="finished 00:45 (next day)"]')).to_contain_text('✓')
            await expect(page.locator('.pc-mobile-shift-kind').filter(has_text='Half day (AM)').first).to_be_visible()
        assert await page.evaluate('document.body.scrollWidth') <= width + 2
        await page.screenshot(path=OUT / f'manager-{width}.png', full_page=True, animations='disabled')
    await select_date(START + timedelta(days=1), 'MK')
    mobile_panel = await page.locator('.pc-assignment-sidebar').bounding_box()
    assert 64 <= mobile_panel['y'] < 200, mobile_panel
    await expect(page.get_by_role('region', name=f'Schedule details {START + timedelta(days=1)} MK')).to_be_visible()
    # Re-selecting the same date must also bring its controls back into view.
    await select_date(START + timedelta(days=1), 'MK')
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
    await expect(calendars.nth(1).locator('.pc-shift').filter(has_text='Mason').locator('.pc-shift-pos').first).to_have_text('Cashier')
    await expect(calendars.nth(1).locator('.pc-shift').filter(has_text='Mason').locator('.pc-shift-pos').first).to_be_visible()
    for width, height in ((1440,900), (1280,800), (1366,768), (1920,1080), (3840,2160)):
        await page.set_viewport_size({'width':width,'height':height})
        await page.wait_for_function("[...document.querySelectorAll('.pc-store-calendars .fc table')].every(table => table.getBoundingClientRect().width <= table.closest('.fc').getBoundingClientRect().width + 1)")
        await expect(page.locator('.pc-store-calendars .fc-daygrid-more-link')).to_have_count(0)
        for calendar in (calendars.nth(0), calendars.nth(1)):
            await expect(calendar.locator('.fc-daygrid-day-events .pc-shift:visible').first).to_be_visible()
        first, second = await calendars.nth(0).bounding_box(), await calendars.nth(1).bounding_box()
        assert second['y'] >= first['y'] + first['height'], (first,second)
        assert abs(first['x'] - second['x']) < 2 and abs(first['width'] - second['width']) < 2, (first,second)
        assert await page.evaluate('document.body.scrollWidth') <= width + 2
        for code in ('DT','MK'):
            day = page.locator(f'.pc-store-calendars section:has(button[aria-label="View {START} {code}"]) td[data-date="{START}"]')
            assert await day.locator('.pc-shift:visible').count() >= 6
        if width == 1366:
            assert await page.locator('.pc-calendar-month .pc-shift:visible').first.evaluate('(el) => el.getBoundingClientRect().height') >= 22
            assert await page.evaluate('document.body.scrollHeight > innerHeight'), 'Month may extend below viewport for readable shifts'
        assert await page.locator('.pc-store-calendars .fc-scroller').evaluate_all('(els) => els.every(e => e.scrollHeight <= e.clientHeight + 2)'), 'Month dates must not need internal scrolling'
        await page.screenshot(path=OUT/f'month-fit-{width}.png', animations='disabled')
        await select_date(START, 'DT')
        await page.wait_for_function("[...document.querySelectorAll('.pc-store-calendars .fc table')].every(table => table.getBoundingClientRect().width <= table.closest('.fc').getBoundingClientRect().width + 1)")
        assert await page.locator('.pc-store-calendars .fc-scroller').evaluate_all('(els) => els.every(e => e.scrollHeight <= e.clientHeight + 2)')
        for calendar in (calendars.nth(0), calendars.nth(1)):
            await expect(calendar.locator('.fc-daygrid-day-events .pc-shift:visible').first).to_be_visible()
        await page.screenshot(path=OUT/f'month-assignment-fit-{width}.png', animations='disabled')
        await page.get_by_role('button', name='Close day details', exact=True).click()
    # Busy days expand in both date-grid views, including on phones.
    for view in ('Two weeks', 'Month'):
        await page.get_by_role('button', name=view, exact=True).click()
        for width in (3840, 1920, 768, 390):
            await page.set_viewport_size({'width':width,'height':2160 if width == 3840 else 980})
            await page.wait_for_function("[...document.querySelectorAll('.pc-store-calendars .fc table')].every(table => table.getBoundingClientRect().width <= table.closest('.fc').getBoundingClientRect().width + 1)")
            await expect(page.locator('.pc-store-calendars .fc-daygrid-more-link')).to_have_count(0)
            for code in ('DT', 'MK'):
                day = page.locator(f'.pc-store-calendars section:has(button[aria-label="View {START} {code}"]) td[data-date="{START}"]')
                shifts = day.locator('.pc-shift:visible, .pc-mobile-shift:visible')
                assert await shifts.count() >= 6
                assert await shifts.evaluate_all('(els) => els.every(el => el.getBoundingClientRect().bottom <= el.closest("td").getBoundingClientRect().bottom)'), 'Every employee must fit inside the day'
            assert await page.evaluate('document.body.scrollWidth') <= width + 2
            await page.screenshot(path=OUT/f'all-people-{view.replace(" ", "-")}-{width}.png', full_page=True, animations='disabled')
    # Representative monthly staffing: four daily at Downtown, two at Markham, plus existing shifts.
    with closing(case.connect()) as con:
        con.execute('DELETE FROM shifts WHERE employee_id IN (205,206,303,304,305,306)')
        con.execute("UPDATE employees SET name=CASE id WHEN 201 THEN 'Cindy' WHEN 202 THEN 'FULIN' WHEN 203 THEN 'Xiao Chen' WHEN 204 THEN 'Zimo' WHEN 301 THEN 'LONG' WHEN 302 THEN 'Xiao Nan' ELSE name END")
        con.execute("UPDATE employees SET color=CASE id WHEN 201 THEN '#6DADE0' WHEN 202 THEN '#C4EC73' WHEN 203 THEN '#E6A06B' WHEN 204 THEN '#DC28C6' WHEN 301 THEN '#397F22' WHEN 302 THEN '#13CCCC' ELSE color END")
        con.commit()
    await page.set_viewport_size({'width':1366,'height':800})
    await page.reload()
    await page.get_by_role('button', name='Month', exact=True).click()
    await select_date(START, 'DT')
    await expect(page.get_by_role('region', name=f'Schedule details {START} DT').get_by_text('Assigned · 6', exact=True)).to_be_visible()
    await expect(page.locator('.pc-store-calendars .fc-daygrid-more-link')).to_have_count(0)
    await page.wait_for_function("[...document.querySelectorAll('.pc-store-calendar-body')].every(el => el.scrollHeight <= el.clientHeight + 2)")
    assert await page.locator('.pc-store-calendars .fc-scroller').evaluate_all('(els) => els.every(e => e.scrollHeight <= e.clientHeight + 2)')
    await page.screenshot(path=OUT/'all-shifts-MONTH-1366.png', animations='disabled')
    # Long notes remain saveable without displacing the checklist or hiding day controls.
    await page.set_viewport_size({'width':1366,'height':700})
    await show_checklist()
    await page.locator('.pc-coverage-notes summary').click()
    await page.get_by_role('textbox', name='Period notes', exact=True).fill('Weekend coverage\nConfirm Celia\nConfirm Mason\nReview trainees\nCheck both stores')
    await page.get_by_role('button', name='Save notes', exact=True).scroll_into_view_if_needed()
    save_notes = page.get_by_role('button', name='Save notes', exact=True)
    await expect(save_notes).to_be_in_viewport(ratio=1)
    assert await save_notes.evaluate('''button => {
        const box = button.getBoundingClientRect()
        return button.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2))
    }'''), 'Save notes must not be covered by the header or another panel'
    await page.get_by_role('button', name='Save notes', exact=True).click()
    await expect(page.get_by_text('Notes saved', exact=True)).to_be_visible()
    assert await page.locator('.pc-assignment-day').evaluate('(el) => el.clientHeight') >= 170
    await page.reload()
    await expect(checklist.get_by_role('checkbox', name='Checked: Jessi', exact=True)).to_be_checked()
    await page.get_by_role('button', name='Month', exact=True).click()
    await page.locator('.pc-coverage-notes summary').click()
    await expect(page.get_by_role('textbox', name='Period notes', exact=True)).to_have_value('Weekend coverage\nConfirm Celia\nConfirm Mason\nReview trainees\nCheck both stores')
    await page.locator('.pc-coverage-notes summary').click()
    await show_checklist(False)
    await page.get_by_role('button', name='Month', exact=True).click()
    # Check the previous month too (August 2026 has six actual calendar weeks).
    await page.set_viewport_size({'width':1280,'height':800})
    await page.get_by_role('button', name='Previous calendar period', exact=True).click()
    previous_date = START.replace(day=1) - timedelta(days=1)
    await select_date(previous_date, 'MK')
    await expect(page.get_by_role('region', name=f'Schedule details {previous_date} MK')).to_be_visible()
    await page.wait_for_function("[...document.querySelectorAll('.pc-store-calendar-body')].every(el => el.scrollHeight <= el.clientHeight + 2)")
    assert await page.locator('.pc-store-calendars .fc-scroller').evaluate_all('(els) => els.every(e => e.scrollHeight <= e.clientHeight + 2)')
    await page.screenshot(path=OUT/'previous-month-fit-1280.png', animations='disabled')
    await page.set_viewport_size({'width':1024,'height':800})
    await expect(page.locator('.pc-store-calendars .fc-daygrid-more-link')).to_have_count(0)
    await page.wait_for_function("[...document.querySelectorAll('.pc-store-calendar-body')].every(el => el.scrollHeight <= el.clientHeight + 2)")
    assert await page.locator('.pc-store-calendars .fc-scroller').evaluate_all('(els) => els.every(e => e.scrollHeight <= e.clientHeight + 2)')
    await context.close()
    context, page = await page_for('staff', 390)
    await page.get_by_role('tab', name='我的班 My shifts', exact=True).click()
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
