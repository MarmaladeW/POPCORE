"""分区盘点 Section count in a real browser against the real Flask app and a disposable database: a
manager saves the suggested six sections, starts the next one with the employee who counts, enters
a blind-box card (its designs belong to 确认款, so the card leaves them out), goes on with Next to
the next card not yet entered, finishes, and reads what changed. Staff do not see it."""
import asyncio
import re
import subprocess
import sys
from contextlib import closing
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5197
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'section-count'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_count_sections import SectionCountTests
    fixture = SectionCountTests()
    fixture.setUp()
    state = {'role': 'manager'}
    context = await browser.new_context(viewport={'width': width, 'height': 900})
    await context.add_init_script(
        "window.__FOUNDATION_AUTH={role:'manager',token:'manager'};"
        f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.dt},code:'DT',name:'Downtown'}}))")
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
        # A live store's 盘点 shows managers the way in.
        await page.goto(BASE + '/count')
        await page.get_by_role('link', name=re.compile('分区盘点 Section count')).click()

        # No sections yet: the suggestion of six, every line in one of them.
        editor = page.get_by_role('region', name='Edit sections')
        await expect(editor.get_by_label(re.compile('^Name of section'))).to_have_count(6)
        await expect(editor.get_by_label('Name of section 2')).to_have_value('盲盒 Blind boxes A')
        await fits(page, f'suggested-{width}.png')
        await editor.get_by_role('button', name='保存分区 Save sections').click()

        sections = page.get_by_role('region', name='Sections')
        await expect(sections.get_by_role('listitem')).to_have_count(6)
        await expect(page.get_by_role('region', name='Next up')).to_contain_text('今天到期 · Due today')
        await fits(page, f'sections-{width}.png')
        assert rows('SELECT COUNT(*) FROM count_sections WHERE retired_at IS NULL') == [(6,)]

        # Start the blind boxes with THE MONSTERS, counted by Amy.
        row = sections.get_by_role('listitem').filter(has_text='盲盒 Blind boxes A')
        await row.get_by_role('button', name='开始 Start').click()
        dialog = page.get_by_role('dialog')
        await dialog.get_by_role('combobox', name='Who is counting').click()
        await page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
                           has_text='Amy').click()
        await dialog.get_by_role('button', name='开始 Start').click()
        await expect(page.get_by_text('已点 0 / 2')).to_be_visible()
        await fits(page, f'count-list-{width}.png')

        # Upstairs the card holds the whole sets; the designs on it are counted with 确认款.
        upstairs = page.get_by_role('region', name='楼上 Upstairs')
        await upstairs.get_by_role('button', name=re.compile('^Labubu Seat')).click()
        card = page.get_by_role('region', name='Count Labubu Seat')
        await expect(card.get_by_label(re.compile('^整端 Whole sets'))).to_be_visible()
        await expect(card.get_by_label(re.compile('^Sisi'))).to_have_count(0)
        await expect(card.get_by_text('确认款 Designs')).to_have_count(0)
        await card.get_by_label(re.compile('^整端 Whole sets')).fill('3')
        await card.get_by_role('button', name='保存 Save').click()
        await expect(card.get_by_role('status')).to_contain_text('已保存 Saved')
        await fits(page, f'count-card-{width}.png')

        # Next goes to the card not entered yet: the floor's boxes.
        await card.get_by_role('button', name='下一个 Next card').click()
        card = page.get_by_role('region', name='Count Labubu Seat')
        await expect(card).to_contain_text('店内 Floor')
        await card.get_by_label(re.compile('^盒数 All boxes')).fill('8')
        await card.get_by_role('button', name='保存 Save').click()
        await expect(card.get_by_role('status')).to_contain_text('库存 10 → 8')
        await card.get_by_role('button', name='下一个 Next card').click()
        await expect(page.get_by_text('已点 2 / 2')).to_be_visible()
        assert rows('SELECT COUNT(*) FROM section_count_saves') == [(2,)]

        # Finish: what changed, who counted and who entered.
        await page.get_by_role('button', name=re.compile('^完成 Finish')).click()
        summary = page.get_by_role('region', name='Summary')
        await expect(summary).to_contain_text('Amy counted')
        await expect(summary).to_contain_text('−2')
        differences = page.get_by_role('region', name='Differences')
        await expect(differences).to_contain_text('Labubu Seat')
        await expect(differences).to_contain_text('10 → 8')
        await fits(page, f'summary-{width}.png')
        assert rows('SELECT COUNT(*) FROM section_counts WHERE finished_at IS NOT NULL') == [(1,)]

        # Staff do not see it.
        state['role'] = 'staff'
        await context.add_init_script("window.__FOUNDATION_AUTH={role:'staff',token:'staff'};")
        await page.goto(BASE + '/count/sections')
        await expect(page.get_by_text('Access Denied')).to_be_visible()
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
    print('PASS: section count — suggested six sections saved, next up, start with the counter, a blind-box card '
          'without its designs, Next to the next card, finish with the differences, staff kept out, at 390 and 1440 px')
