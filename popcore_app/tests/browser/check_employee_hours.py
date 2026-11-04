"""转正 marks, the per-employee monthly hours page and the 员工 list + detail, against the real
Flask app and a disposable database (owner, 2026-11-03). The browser clock is fixed on
2026-10-28, inside the October wage period (Oct 4 – Nov 3)."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import json
import re
import subprocess
import sys
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

sys.path.insert(0, str(HERE.parent))
from support import IsolatedApiCase
from blueprints import users as users_blueprint

PORT = 5197
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'employee-hours'
NOW = datetime(2026, 10, 28, 17, tzinfo=timezone.utc)  # 13:00 in Toronto
USERS = [{'id': 'auth0|admin', 'username': 'admin1', 'role': 'admin', 'is_active': 1,
          'created_at': '2026-08-01 10:00:00', 'last_login': '2026-10-28 09:02:00'},
         {'id': 'auth0|jessi', 'username': 'jessi', 'role': 'staff', 'is_active': 1,
          'created_at': '2026-08-03 14:20:00', 'last_login': '2026-10-27 11:57:00'},
         {'id': 'auth0|amy', 'username': 'amy', 'role': 'staff', 'is_active': 1,
          'created_at': '2026-09-20 12:00:00', 'last_login': ''}]


def seed(case):
    case.app.register_blueprint(users_blueprint.bp)
    with closing(case.connect()) as con:
        dt = case.store_id
        mk = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
        con.executescript(f"""
            INSERT INTO employees(id,auth0_id,name,color,regular_since) VALUES
              (201,'auth0|jessi','Jessi','#5B7FA6','2026-10-15'),
              (202,'auth0|amy','Amy','#A0698A',NULL),
              (203,'auth0|admin','Admin','#7A6FB0',NULL);
            INSERT INTO employees(id,auth0_id,name,color,is_trainee) VALUES (204,'trainee|lin','Lin','#C0874F',1);
            INSERT INTO employee_stores(employee_id,store_id) VALUES (201,{dt}),(201,{mk}),(202,{dt});
        """)
        for employee, day, start, end, actual, store, position in (
            (201, '2026-10-04', '12:00', '20:00', None, dt, ''),
            (201, '2026-10-06', '11:00', '19:00', '19:20', dt, ''),
            (201, '2026-10-07', '12:00', '16:00', None, mk, ''),
            (201, '2026-10-14', '12:00', '22:00', None, dt, ''),
            (201, '2026-10-15', '12:00', '22:00', '00:30', dt, ''),
            (201, '2026-10-21', '15:00', '19:00', None, dt, 'Cashier'),
            (201, '2026-10-28', '12:00', '20:00', None, dt, ''),
            (201, '2026-11-02', '12:00', '20:00', None, dt, ''),
            (202, '2026-10-06', '12:00', '20:00', None, dt, ''),
            (202, '2026-10-28', '12:00', '17:00', None, dt, ''),
        ):
            con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,actual_end_time,assigned_by,store_id,position)
                           VALUES (?,?,?,?,?,'fixture',?,?)""", (employee, day, start, end, actual, store, position))
        con.commit()


async def open_as(browser, case, role, width, store='ALL'):
    context = await browser.new_context(viewport={'width': width, 'height': 900})
    selected = {'id': 0, 'code': 'ALL', 'name': 'All Stores'} if store == 'ALL' \
        else {'id': case.store_id, 'code': 'DT', 'name': 'Downtown'}
    await context.add_init_script(
        f"window.__FOUNDATION_AUTH={{role:'{role}',token:'{role}'}};"
        f"localStorage.setItem('popcore_selected_store',{json.dumps(json.dumps(selected))})")
    page = await context.new_page()
    await page.clock.install(time=NOW)
    page.on('pageerror', lambda error: print('Browser error:', error))

    async def flask_boundary(route):
        parsed = urlsplit(route.request.url)
        if parsed.hostname not in ('127.0.0.1', 'localhost'):
            await route.abort(); return
        if not parsed.path.startswith('/api/'):
            await route.continue_(); return
        if parsed.path == '/api/users':  # Auth0's account list
            await route.fulfill(json=USERS); return
        body = route.request.post_data_json if route.request.post_data else None
        response = case.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                    method=route.request.method, headers=case.headers(role), json=body)
        await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
    await page.route('**/*', flask_boundary)
    return context, page


async def fits(page, name):
    width = page.viewport_size['width']
    assert await page.evaluate('document.documentElement.scrollWidth') <= width + 2, name
    await page.screenshot(path=OUT / name, full_page=True, animations='disabled')


async def monthly_hours(browser, case, width):
    phone = width < 768
    context, page = await open_as(browser, case, 'manager', width)
    try:
        await page.goto(BASE + '/schedule')
        await page.get_by_role('tab', name='月工时 Monthly report').click()
        jessi = page.get_by_role('button', name='Jessi: 每班工时 Hours by shift', exact=True)
        await expect(jessi).to_be_visible()
        # Jessi is 转正 from Oct 15, inside this period; Amy is not.
        await expect(jessi.locator('.pc-regular-mark')).to_have_count(1)
        await expect(jessi).to_contain_text('10/15 起')
        await expect(page.get_by_role('button', name='Amy: 每班工时 Hours by shift', exact=True).locator('.pc-regular-mark')).to_have_count(0)
        jessi_row = page.locator('.ant-table-row').filter(has=jessi)
        await expect(jessi_row).to_contain_text('62.83h')
        await fits(page, f'report-{width}.png')

        await jessi.click()
        slip = page.get_by_role('article', name='Jessi 2026-10 hours')
        await expect(slip).to_be_visible()
        await expect(slip.locator('.pc-slip-total')).to_contain_text('62.83 h')
        await expect(slip.locator('.pc-slip-total')).to_contain_text('8 个班')
        await expect(slip).to_contain_text('10/04 – 11/03 · 全部门店 All stores')
        await expect(slip.locator('.pc-slip-regular')).to_contain_text('10/15 转正 · 转正前 Before 30.33 h · 转正后 After 32.5 h')
        weeks = slip.locator('.pc-slip-week')
        await expect(weeks).to_have_count(6)
        await expect(weeks.nth(0)).to_contain_text('第 1 周 Week 1')
        await expect(weeks.nth(0)).to_contain_text('10/04')
        await expect(weeks.nth(0)).to_contain_text('8 h')
        await expect(weeks.nth(1)).to_contain_text('10/05 – 10/11')
        await expect(weeks.nth(1)).to_contain_text('12.33 h')
        await expect(weeks.nth(2)).to_contain_text('22.5 h')
        await expect(weeks.nth(5)).to_contain_text('11/02 – 11/03')
        # The 转正 line sits right before the first shift on Oct 15.
        boundary = slip.locator('.pc-slip-boundary')
        await expect(boundary).to_have_count(1)
        await expect(boundary.locator('xpath=following-sibling::tr[1]')).to_contain_text('Thu 10/15')
        late = slip.get_by_role('row').filter(has_text='Tue 10/06')
        await expect(late).to_contain_text('DT')
        await expect(late).to_contain_text('11:00–19:20')
        await expect(late).to_contain_text('原定 Planned 19:00')
        await expect(late).to_contain_text('8.33')
        await expect(slip.get_by_role('row').filter(has_text='Thu 10/15')).to_contain_text('12:00–00:30 +1')
        await expect(slip.get_by_role('row').filter(has_text='Wed 10/21')).to_contain_text('Cashier')
        await expect(slip.locator('tfoot')).to_contain_text('62.83 h')
        await slip.screenshot(path=OUT / f'slip-{width}.png', animations='disabled')
        await fits(page, f'slip-page-{width}.png')

        # Step through people in the report's order (Admin, Amy, Jessi, Lin) without going back.
        await page.get_by_role('button', name='下一位 Next person').click()
        lin = page.get_by_role('article', name='Lin 2026-10 hours')
        await expect(lin).to_contain_text('实习生 Trainee')
        await expect(lin.locator('.pc-slip-none')).to_have_count(6)
        await expect(page.get_by_role('button', name='下一位 Next person')).to_be_disabled()
        await page.get_by_role('button', name='上一位 Previous person').click()
        await page.get_by_role('button', name='上一位 Previous person').click()
        amy = page.get_by_role('article', name='Amy 2026-10 hours')
        await expect(amy.locator('.pc-slip-total')).to_contain_text('13 h')
        await expect(amy.locator('.pc-regular-mark')).to_have_count(0)
        await page.get_by_role('button', name='月工时 Monthly report').click()
        await expect(jessi).to_be_visible()
        if phone:  # the phone's back gesture also leaves the slip
            await jessi.click()
            await expect(slip).to_be_visible()
            await page.go_back()
            await expect(jessi).to_be_visible()
    finally:
        await context.close()

    # One store: the slip follows the report's scope and drops the store column.
    context, page = await open_as(browser, case, 'manager', width, store='DT')
    try:
        await page.goto(BASE + '/schedule')
        await page.get_by_role('tab', name='月工时 Monthly report').click()
        await page.get_by_role('button', name='Jessi: 每班工时 Hours by shift', exact=True).click()
        slip = page.get_by_role('article', name='Jessi 2026-10 hours')
        await expect(slip.locator('.pc-slip-total')).to_contain_text('58.83 h')
        await expect(slip).to_contain_text('10/04 – 11/03 · DT')
        await expect(slip.get_by_role('columnheader', name='门店 Store')).to_have_count(0)
        await expect(slip.get_by_role('row').filter(has_text='Wed 10/07')).to_have_count(0)
    finally:
        await context.close()


async def calendar_marks(browser, case):
    context, page = await open_as(browser, case, 'manager', 1440, store='DT')
    try:
        await page.goto(BASE + '/schedule')
        await page.get_by_role('button', name='Month', exact=True).click()
        calendar = page.locator('.pc-store-calendars')
        before = calendar.locator('td[data-date="2026-10-14"] .pc-shift-bar').filter(has_text='Jessi')
        after = calendar.locator('td[data-date="2026-10-21"] .pc-shift-bar').filter(has_text='Jessi')
        await expect(before).to_have_count(1)
        await expect(before.locator('.pc-regular-mark')).to_have_count(0)
        await expect(after.locator('.pc-regular-mark')).to_have_count(1)
        await expect(after).to_have_attribute('aria-label', re.compile('转正 Regular'))
        amy = calendar.locator('td[data-date="2026-10-28"] .pc-shift-bar').filter(has_text='Amy')
        await expect(amy.locator('.pc-regular-mark')).to_have_count(0)
        await expect(page.get_by_text('转正 Regular (from their 转正 date)')).to_be_attached()
        await page.screenshot(path=OUT / 'calendar-1440.png', full_page=True, animations='disabled')
    finally:
        await context.close()


async def users_page(browser, case, width):
    phone = width < 768
    context, page = await open_as(browser, case, 'admin', width)
    try:
        await page.goto(BASE + '/users')
        people = page.get_by_role('region', name='员工列表 People')
        jessi = people.get_by_role('button', name=re.compile(r'^Jessi'))
        amy = people.get_by_role('button', name=re.compile(r'^Amy'))
        await expect(jessi.locator('.pc-regular-mark')).to_have_count(1)
        await expect(jessi).to_contain_text('排班 DT MK')
        await expect(amy.locator('.pc-regular-mark')).to_have_count(0)
        await expect(amy).to_contain_text('从未登录')
        if phone:
            await expect(page.get_by_role('checkbox')).to_have_count(0)  # the list alone
            await fits(page, f'users-list-{width}.png')
        await jessi.click()
        detail = page.get_by_role('region', name='Jessi 详情 Details')
        await expect(detail).to_be_visible()
        await expect(detail.get_by_role('checkbox', name='jessi: DT scheduling', exact=True)).to_be_checked()
        await expect(detail.get_by_role('checkbox', name='jessi: MK scheduling', exact=True)).to_be_checked()
        await expect(detail.get_by_label('转正日期 Regular since')).to_have_value('2026-10-15')
        await expect(detail.locator('.pc-ud-state')).to_contain_text('已转正')
        await fits(page, f'users-jessi-{width}.png')
        if phone:
            await page.go_back()
        await amy.click()
        detail = page.get_by_role('region', name='Amy 详情 Details')
        picker = detail.get_by_label('转正日期 Regular since')
        await picker.click()
        if phone:  # read-only on phone (no keyboard): pick from the calendar
            await page.locator('.ant-picker-cell[title="2026-10-20"]').click()
        else:
            await picker.fill('2026-10-20')
            await picker.press('Enter')
        await expect(page.get_by_text('转正日期已保存 · 2026-10-20', exact=True)).to_be_visible()
        await expect(detail.locator('.pc-ud-state')).to_contain_text('已转正')
        with closing(case.connect()) as con:
            assert con.execute('SELECT regular_since FROM employees WHERE id=202').fetchone()[0] == '2026-10-20'
            con.execute('UPDATE employees SET regular_since=NULL WHERE id=202'); con.commit()
        # Scheduling stores tick in the same grid as access.
        mk = detail.get_by_role('checkbox', name='amy: MK scheduling', exact=True)
        await mk.check()
        await expect(mk).to_be_checked()
        with closing(case.connect()) as con:
            codes = [r[0] for r in con.execute("""SELECT s.code FROM employee_stores es JOIN stores s ON s.id=es.store_id
                                                  WHERE es.employee_id=202 ORDER BY s.code""")]
            assert codes == ['DT', 'MK'], codes
            con.execute("DELETE FROM employee_stores WHERE employee_id=202 AND store_id=(SELECT id FROM stores WHERE code='MK')")
            con.commit()
        await fits(page, f'users-amy-{width}.png')
    finally:
        await context.close()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    case = IsolatedApiCase(); case.setUp()
    seed(case)
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
                        await monthly_hours(browser, case, width)
                        await users_page(browser, case, width)
                    await calendar_marks(browser, case)
                except Exception:
                    for context in browser.contexts:
                        if context.pages:
                            await context.pages[-1].screenshot(path=OUT / 'failure.png', full_page=True)
                            break
                    raise
                finally:
                    await browser.close()
        finally:
            process.terminate(); process.wait(timeout=10); case.tearDown()


if __name__ == '__main__':
    asyncio.run(main())
    print('PASS: 正 marks follow the 转正 date in the calendar and monthly report; each name opens hours by shift '
          'and week that add up to the report; 员工 is a list plus detail at 1440 and 390.')
