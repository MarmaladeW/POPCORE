"""待盘点 Count next time in a real browser against the real Flask app and a disposable database (owner,
2026-11-22: "mark the eevee as need to be counted next time we count"). A manager marks the Nov 20 Eevee
notice to count on the floor and upstairs; 盘点 lists it first on the floor and the card says which item;
counting it there closes that mark and the notice says so. A section count that starts lists the open
marks first, also from another section. A product's stock details mark and unmark it too."""
import asyncio
import re
import subprocess
import sys
from contextlib import closing
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5198
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'count-marks'
REASON = '日报 11/20 楼下不够 × 1'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_count_marks import CountMarkTests
    fixture = CountMarkTests()
    fixture.setUp()
    state = {'role': 'manager'}
    context = await browser.new_context(viewport={'width': width, 'height': 900})
    await context.add_init_script(
        "window.__FOUNDATION_AUTH={role:'manager',token:'manager'};"
        f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.dt},code:'DT',name:'Downtown'}}));"
        "localStorage.setItem('pc-count-location-DT','floor')")
    page = await context.new_page()

    async def flask_boundary(route):
        parsed = urlsplit(route.request.url)
        if parsed.hostname not in ('127.0.0.1', 'localhost'):
            await route.abort(); return
        if not parsed.path.startswith('/api/'):
            await route.continue_(); return
        headers = fixture.headers(state['role'])
        key = route.request.headers.get('idempotency-key')
        if key:
            headers['Idempotency-Key'] = key
        body = route.request.post_data_json if route.request.post_data else None
        response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                       method=route.request.method, headers=headers, json=body)
        await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
    await page.route('**/*', flask_boundary)

    def rows(sql, *params):
        with closing(fixture.connect()) as con:
            return [tuple(row) for row in con.execute(sql, params)]

    try:
        # The notice: 下次盘点 offers the floor and upstairs (3 there), with the notice as the reason.
        await page.goto(BASE + '/sales/report-stock')
        await page.get_by_role('button', name='下次盘点 Count next time').click()
        form = page.get_by_role('form', name=re.compile('下次盘点 Count .*Eevee next time'))
        await expect(form.get_by_role('checkbox', name='楼下 Floor')).to_be_checked()
        await expect(form.get_by_role('checkbox', name='楼上 Upstairs')).to_be_checked()
        await expect(form.get_by_role('checkbox', name='Display')).not_to_be_checked()
        await expect(form.get_by_label('为什么 Why')).to_have_value(REASON)
        await fits(page, f'notice-form-{width}.png')
        await form.get_by_role('button', name='标记 Mark').click()
        marked = page.get_by_role('list', name='待盘点 Count next time')
        # One line per reason, then each place it is marked at.
        await expect(marked.get_by_role('listitem')).to_have_count(1)
        await expect(marked).to_contain_text('待盘点 楼下 Floor')
        await expect(marked).to_contain_text('楼上 Upstairs')
        await expect(marked).to_contain_text(f'{REASON} · Mia Manager')
        await expect(page.get_by_role('button', name='下次盘点 Count next time')).to_be_visible()
        await fits(page, f'notice-marked-{width}.png')
        assert rows("SELECT l.code, m.source_id FROM count_marks m JOIN inventory_locations l ON l.id=m.location_id "
                    "WHERE m.status='open' ORDER BY l.code") == [('floor', fixture.notice), ('upstairs', fixture.notice)]

        # 盘点 on the floor: the mark first, above the search; the place buttons say where marks wait.
        state['role'] = 'staff'
        await page.goto(BASE + '/count')
        await expect(page.get_by_role('button', name=re.compile('店内 Floor'))).to_contain_text('1')
        listed = page.get_by_role('region', name=re.compile('^待盘点'))
        await expect(listed.get_by_role('button')).to_have_count(1)
        await expect(listed).to_contain_text('PK0008-02')
        await expect(listed).to_contain_text(REASON)
        await fits(page, f'count-list-{width}.png')
        await listed.get_by_role('button').click()
        card = page.get_by_role('region', name='Count 宝可梦自然')
        await expect(card.get_by_text(f'待盘点 {REASON}')).to_be_visible()
        await fits(page, f'count-card-{width}.png')
        await card.get_by_label(re.compile('^Eevee')).fill('0')
        await card.get_by_role('button', name='保存 Save').click()
        await expect(card.get_by_role('status')).to_contain_text('已保存 Saved')
        await expect(card.get_by_text(f'待盘点 {REASON}')).to_have_count(0)
        await card.get_by_role('button', name='下一个 Next').click()
        await expect(page.get_by_role('region', name=re.compile('^待盘点'))).to_have_count(0)
        assert rows("SELECT l.code, m.status FROM count_marks m JOIN inventory_locations l ON l.id=m.location_id "
                    "ORDER BY l.code") == [('floor', 'counted'), ('upstairs', 'open')]

        # The notice says the floor was counted, so the manager can check it.
        state['role'] = 'manager'
        await page.goto(BASE + '/sales/report-stock')
        await expect(page.get_by_text('已点 Counted 楼下 Floor')).to_be_visible()
        await expect(page.get_by_text('Sam Staff', exact=False).first).to_be_visible()
        await expect(page.get_by_role('list', name='待盘点 Count next time').get_by_role('listitem')).to_have_count(1)
        await fits(page, f'notice-counted-{width}.png')

        # A section count lists the open marks first: Eevee upstairs is a design, not in 其他, and still comes.
        fixture.client.put('/api/counting/sections', headers=fixture.headers('manager'), json={
            'store_code': 'DT', 'version': 0, 'sections': [
                {'name': '确认款 Designs', 'parts': [{'kind': 'design', 'group_key': '*'}]},
                {'name': '其他 The rest', 'parts': [{'kind': kind, 'group_key': '*'} for kind in ('display', 'blind_box', 'other')]}]})
        await page.goto(BASE + '/count/sections')
        await expect(page.get_by_text(re.compile('The next section count you start lists it first'))).to_be_visible()
        await fits(page, f'sections-{width}.png')
        await page.get_by_role('region', name='Sections').get_by_role('listitem').filter(has_text='其他 The rest') \
            .get_by_role('button', name='开始 Start').click()
        dialog = page.get_by_role('dialog')
        await dialog.get_by_role('combobox', name='Who is counting').click()
        await page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
                           has_text='Amy').click()
        await dialog.get_by_role('button', name='开始 Start').click()
        first = page.get_by_role('region', name='Count next time')
        await expect(first).to_contain_text('宝可梦自然')
        await expect(first).to_contain_text('待盘点 楼上 Upstairs')
        await expect(first).to_contain_text(f'Eevee: {REASON}')
        regions = await page.locator('section.pc-panel').evaluate_all('nodes => nodes.map(node => node.getAttribute("aria-label"))')
        assert regions[0] == 'Count next time', regions
        await fits(page, f'section-count-{width}.png')
        await first.get_by_role('button', name=re.compile('^宝可梦自然')).click()
        card = page.get_by_role('region', name='Count 宝可梦自然')
        await expect(card.get_by_label(re.compile('^Eevee'))).to_be_visible()
        await expect(card.get_by_text(f'待盘点 {REASON}')).to_be_visible()

        # A product's stock details: its marks at this store, unmark one, mark it again.
        series = rows('SELECT series_id FROM products WHERE id=?', fixture.eevee)[0][0]
        await page.goto(BASE + f'/stock?series_id={series}')
        await page.get_by_label('Stock details for Eevee', exact=True).click()
        drawer = page.get_by_role('dialog', name='Stock details', exact=True)
        await expect(drawer.get_by_role('list', name='待盘点 Count next time')).to_contain_text('待盘点 楼上 Upstairs')
        await drawer.get_by_role('button', name='取消 Unmark 楼上 Upstairs').click()
        await expect(drawer.get_by_role('list', name='待盘点 Count next time')).to_have_count(0)
        await drawer.get_by_role('button', name='下次盘点 Count next time').click()
        form = drawer.get_by_role('form', name='下次盘点 Count Eevee next time')
        # antd's controlled checkbox re-renders after the click, so click and then expect it ticked.
        await form.get_by_role('checkbox', name='楼上 Upstairs').click()
        await expect(form.get_by_role('checkbox', name='楼上 Upstairs')).to_be_checked()
        await form.get_by_label('为什么 Why').fill('Box looked opened')
        await fits(page, f'product-form-{width}.png')
        await form.get_by_role('button', name='标记 Mark').click()
        await expect(drawer.get_by_role('list', name='待盘点 Count next time')).to_contain_text('Box looked opened · Mia Manager')
        assert rows("SELECT status, reason FROM count_marks WHERE location_id=? ORDER BY id", fixture.loc['upstairs']) == [
            ('removed', REASON), ('open', 'Box looked opened')]
        await fits(page, f'product-marked-{width}.png')

        # Staff see the marks on 盘点 but cannot mark.
        state['role'] = 'staff'
        assert fixture.mark(fixture.card, ['floor'], role='staff').status_code == 403
    finally:
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
    print('PASS: count next time — a notice marks Eevee on the floor and upstairs, 盘点 lists it first and the card '
          'names the item, counting closes the floor mark and the notice says so, a section count lists marks first '
          'from another section, stock details unmark and mark, at 390 and 1440 px')
