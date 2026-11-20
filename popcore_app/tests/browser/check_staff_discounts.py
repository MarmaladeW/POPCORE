"""员工折扣 Staff discounts in a real browser against the real Flask app and a disposable database (owner, 2026-11-19:
"each 正式员工 and above have 4 discount per month, we wanna keep track of that"): a manager sees everyone in rank order
with their uses out of 4, someone over 4 in red, a 培训员工 marked 不适用, the month's money per store, DT's two real
日报 lines (Celia's counted, "zm" waiting); picks Zimo for "zm", marks a record 不算 and counts it again, and adds a
use by hand where ten pieces of one product is one use. Staff see the page and change nothing; viewers cannot open it."""
import asyncio
import json
import subprocess
import sys
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5196
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'staff-discounts'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_staff_discounts import MANAGER, StaffDiscountCase
    from checkout_access import business_date
    fixture = StaffDiscountCase()
    fixture.setUp()
    contexts = []
    month = business_date()[:7]
    try:
        with fixture.connect() as con:
            # The fixture's real lines (Nov 5 and Nov 13) move into this month, so the page opens on them.
            con.execute("UPDATE daily_report_metadata SET date = ? || substr(date, 8)", (month,))
        # Kai bought five different products at MK (one over 4); Tina, a 培训员工, used one.
        for name, store, items, amount in (
                ('Kai', 'MK', [{'name': f'Product {letter}', 'qty': 1} for letter in 'ABCDE'], '100'),
                ('Tina', 'DT', [{'name': 'Twinkle', 'qty': 1}], None)):
            response = fixture.client.post('/api/staff-discounts', headers=MANAGER, json={
                'employee_id': fixture.people[name], 'date': f'{month}-02', 'store': store, 'items': items,
                'amount': amount, 'payment': 'card'})
            assert response.status_code == 200, response.get_json()

        async def open_as(role):
            context = await browser.new_context(viewport={'width': width, 'height': 900})
            contexts.append(context)
            await context.add_init_script(
                f"window.__FOUNDATION_AUTH={{role:'{role}',token:'{role}'}};"
                f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.store_id},code:'DT',name:'Downtown Toronto'}}));")
            page = await context.new_page()

            async def flask_boundary(route):
                parsed = urlsplit(route.request.url)
                if parsed.hostname not in ('127.0.0.1', 'localhost'):
                    await route.abort(); return
                if not parsed.path.startswith('/api/'):
                    await route.continue_(); return
                body = route.request.post_data_json if route.request.post_data else None
                headers = {'Authorization': f'Bearer {role}'}
                if route.request.headers.get('idempotency-key'):
                    headers['Idempotency-Key'] = route.request.headers['idempotency-key']
                response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                               method=route.request.method, headers=headers, json=body)
                await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
            await page.route('**/*', flask_boundary)
            return page

        page = await open_as('manager')
        await page.goto(BASE + '/staff-discounts')
        people = page.get_by_role('region', name='每人 People')
        await expect(people.locator('.pc-sd-person strong')).to_have_text(['Mia', 'Ben', 'Kai', 'Amy', 'Celia', 'Zimo', 'Tina'])
        kai = people.locator('.pc-sd-person').filter(has_text='Kai')
        await expect(kai.get_by_text('超出 1 次 Over by 1')).to_be_visible()
        await expect(kai.locator('.pc-sd-count b')).to_have_css('color', 'rgb(180, 50, 42)')  # --status-out-ink
        celia = people.locator('.pc-sd-person').filter(has_text='Celia')
        await expect(celia.get_by_text('剩 3 次 3 left')).to_be_visible()
        await expect(people.locator('.pc-sd-person').filter(has_text='Tina').get_by_text('不适用 Not eligible')).to_be_visible()
        waiting = page.get_by_role('region', name='待处理 Waiting for a manager')
        await expect(waiting.get_by_text('「zm」')).to_be_visible()
        await expect(waiting.get_by_text('谁？ Pick the person')).to_be_visible()
        uses = page.get_by_role('region', name='本月记录 Uses this month')
        await expect(uses.get_by_text('Angry Molly 迪士尼公主毛绒 ×2')).to_be_visible()
        money = page.locator('.pc-sd-figures')
        await expect(money.locator('.pc-sd-total strong')).to_have_text('$172.07')
        await expect(money.get_by_text('$72.07')).to_be_visible()   # DT: Celia 54 + zm 18.07
        await expect(money.get_by_text('$100.00')).to_be_visible()  # MK: Kai
        await fits(page, f'manager-{width}.png')
        await waiting.screenshot(path=OUT / f'waiting-{width}.png', animations='disabled')

        # "zm" is Zimo: picked once, remembered.
        await waiting.get_by_role('button', name='选人 Pick').click()
        dialog = page.get_by_role('dialog', name='选人 Pick the person')
        await expect(dialog.get_by_text('以后「zm」都算这个人', exact=False)).to_be_visible()
        await dialog.locator('#pc-sd-fix-person').click()
        await page.keyboard.type('Zimo')
        await page.keyboard.press('Enter')
        await dialog.get_by_role('button', name='保存 Save').click()
        await expect(dialog).to_have_count(0)
        await expect(waiting.get_by_text('没有待处理')).to_be_visible()
        await expect(people.locator('.pc-sd-person').filter(has_text='Zimo').get_by_text('剩 3 次 3 left')).to_be_visible()
        with fixture.connect() as con:
            assert [tuple(row) for row in con.execute('SELECT alias, employee_id FROM staff_discount_aliases')] == \
                [('zm', fixture.people['Zimo'])]

        # 不算 with a reason, then counted again.
        row = uses.locator('.pc-sd-use').filter(has_text='Angry Molly')
        await row.get_by_role('button', name='不算 Ignore').click()
        dialog = page.get_by_role('dialog', name='不算 Ignore this record')
        await dialog.get_by_label('为什么不算 Reason').fill('重复了 written twice')
        await dialog.get_by_role('button', name='不算 Ignore').click()
        ignored = page.get_by_role('region', name='不算 Ignored')
        await expect(ignored.get_by_text('重复了 written twice', exact=False)).to_be_visible()
        await expect(celia.get_by_text('剩 4 次 4 left')).to_be_visible()
        await ignored.get_by_role('button', name='算回 Count again').click()
        await expect(celia.get_by_text('剩 3 次 3 left')).to_be_visible()
        await expect(ignored).to_have_count(0)

        # Added by hand: ten pieces of one product is one use.
        await uses.get_by_role('button', name='手动添加 Add by hand').click()
        dialog = page.get_by_role('dialog', name='手动添加 Add a use by hand')
        await dialog.locator('#pc-sd-add-person').click()
        await page.keyboard.type('Amy')
        await page.keyboard.press('Enter')
        await dialog.get_by_label('日期 Date').fill(f'{month}-03')
        await dialog.get_by_label('Product 1', exact=True).fill('Smiski Hipper')
        await dialog.get_by_label('Quantity of product 1').fill('10')
        await dialog.get_by_label('金额 Amount paid').fill('30')
        await expect(dialog.get_by_text('Products · 1 use')).to_be_visible()
        await fits(page, f'add-{width}.png')
        await dialog.get_by_role('button', name='添加 Add').click()
        await expect(dialog).to_have_count(0)
        added = uses.locator('.pc-sd-use').filter(has_text='Smiski Hipper ×10')
        await expect(added.get_by_text('手动', exact=False)).to_be_visible()
        await expect(people.locator('.pc-sd-person').filter(has_text='Amy').get_by_text('剩 3 次 3 left')).to_be_visible()
        await expect(money.locator('.pc-sd-total strong')).to_have_text('$202.07')
        await uses.screenshot(path=OUT / f'uses-{width}.png', animations='disabled')
        with fixture.connect() as con:
            assert json.loads(con.execute("SELECT items FROM staff_discount_uses WHERE source='manual' AND employee_id=?",
                                          (fixture.people['Amy'],)).fetchone()[0]) == [{'name': 'Smiski Hipper', 'qty': 10}]

        # Staff see who has uses left, and change nothing; no money is shown.
        page = await open_as('staff')
        await page.goto(BASE + '/staff-discounts')
        people = page.get_by_role('region', name='每人 People')
        await expect(people.locator('.pc-sd-person').filter(has_text='Zimo').get_by_text('剩 3 次 3 left')).to_be_visible()
        await expect(page.get_by_role('region', name='本月记录 Uses this month').get_by_text('Smiski Hipper ×10')).to_be_visible()
        for name in ('修改 Fix', '不算 Ignore', '手动添加 Add by hand', '选人 Pick'):
            await expect(page.get_by_role('button', name=name)).to_have_count(0)
        await expect(page.locator('.pc-sd-figures')).to_have_count(0)
        await expect(page.get_by_text('$', exact=False)).to_have_count(0)
        if width > 600:
            rail = page.get_by_role('navigation', name='Primary navigation')
            await expect(rail.get_by_role('link', name='员工折扣 Staff discounts')).to_be_visible()
        else:
            await page.goto(BASE + '/more')
            await expect(page.get_by_role('navigation', name='More navigation').get_by_role('link', name='员工折扣', exact=False)).to_be_visible()
            await page.goto(BASE + '/staff-discounts')
            await expect(page.get_by_role('region', name='每人 People')).to_be_visible()
        await fits(page, f'staff-{width}.png')

        # A 查看者 Viewer cannot open it.
        page = await open_as('viewer')
        await page.goto(BASE + '/staff-discounts')
        await expect(page.get_by_text("You don't have permission", exact=False)).to_be_visible()
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
    print('PASS: staff discounts in rank order with uses out of 4, over 4 in red, 不适用 for a 培训员工, money per store, '
          'the real 日报 lines, picking Zimo for "zm" and remembering it, 不算 and counting again, adding by hand '
          '(ten pieces one use), staff reading without buttons or money, a viewer refused, at 390 and 1440 px')
