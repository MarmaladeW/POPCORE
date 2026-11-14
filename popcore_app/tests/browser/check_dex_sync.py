"""图鉴同步 Dex sync, step 3, in a real browser against the real Flask app, a disposable database and the pretend
website of test_dex_sync.py (owner, 2026-11-14: "yes build the POPCORE -> popcore.ca sync"). Off: the first send's
preview with the deletes by name; the admin's turn-on is refused plainly while the website's half is missing and needs
the typed count of deletes. On: a refused entry with the website's reason and Retry, and the entry page's state with
Upload picture for managers only. Widths 390 and 1440, no sideways overflow."""
import asyncio
import subprocess
import sys
from contextlib import closing
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5199
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'dex-sync'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_dex_sync import ACTOR, DexSyncTests
    import dex_book
    fixture = DexSyncTests()
    fixture.setUp()
    company, ip, series, product, designs = fixture.book(designs=2, link=False)
    old = fixture.add('series', 'Retired Series', ip)
    with closing(fixture.connect()) as con:
        dex_book.set_website_number(con, old, 88, 0, ACTOR)
    fixture.remove(old)
    contexts = []

    async def open_as(role):
        context = await browser.new_context(viewport={'width': width, 'height': 900})
        contexts.append(context)
        await context.add_init_script(f"window.__FOUNDATION_AUTH={{role:'{role}',token:'{role}'}};")
        page = await context.new_page()

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
        return page

    try:
        page = await open_as('admin')
        await page.goto(BASE + '/dex')
        await page.get_by_role('link', name='Dex sync').click()
        panel = page.get_by_role('region', name='Dex sync')
        await expect(panel.get_by_text('关 Off', exact=True)).to_be_visible()
        first = page.get_by_role('region', name='First send')
        await expect(first.get_by_text('新建 Created on the website')).to_be_visible()
        deletes = first.get_by_role('region', name='Deletes')
        await expect(deletes.get_by_text('Retired Series')).to_be_visible()
        await expect(deletes.get_by_text('POP MART › HIRONO', exact=False)).to_be_visible()
        await fits(page, f'off-{width}.png')

        # Turning on: the typed count of deletes, and a plain refusal while the website's half is missing.
        fixture.site.missing = True
        await panel.get_by_role('button', name='开启 Turn on').click()
        dialog = page.get_by_role('dialog')
        ok = dialog.get_by_role('button', name='开启 Turn on')
        await expect(ok).to_be_disabled()
        await dialog.get_by_label('Number of deletes').fill('1')
        await ok.click()
        await expect(dialog.get_by_text('not there yet', exact=False)).to_be_visible()
        await fits(page, f'missing-{width}.png')
        await dialog.get_by_role('button', name='取消 Cancel').click()
        await expect(panel.get_by_text('The website’s half of the book sync is not there yet', exact=False)).to_be_visible()
        assert fixture.status()['enabled'] is False

        fixture.site.missing = False
        fixture.site.refuse[series] = 'bad_name'
        await panel.get_by_role('button', name='开启 Turn on').click()
        dialog = page.get_by_role('dialog')
        await dialog.get_by_label('Number of deletes').fill('1')
        await dialog.get_by_role('button', name='开启 Turn on').click()
        await expect(page.get_by_role('dialog')).to_have_count(0)
        await expect(panel.get_by_text('开 On', exact=True)).to_be_visible()
        fixture.run_pass()

        page = await open_as('manager')
        await page.goto(BASE + '/dex/sync')
        refused = page.get_by_role('region', name='Refused by the website')
        await expect(refused.get_by_text('bad_name: Not taken.', exact=False)).to_be_visible()
        await expect(page.get_by_role('region', name='Entries').get_by_text('等上级 Behind a refused entry')).to_be_visible()
        await expect(page.get_by_role('region', name='Dex sync').get_by_role('button', name='关闭 Turn off')).to_have_count(0)
        await fits(page, f'on-{width}.png')
        del fixture.site.refuse[series]
        await refused.get_by_role('button', name='Retry Echo Series').click()
        await expect(page.get_by_text('Echo Series goes again with the next pass')).to_be_visible()
        fixture.run_pass()
        assert fixture.status()['counts']['refused'] == 0

        # The entry page: its state, and Upload picture for managers only.
        await page.goto(BASE + f'/dex/{product}')
        state = page.get_by_label('Dex sync state')
        await expect(state).to_contain_text('已发送 Sent')
        await expect(page.get_by_role('button', name='上传图片 Upload picture')).to_be_visible()
        await fits(page, f'entry-{width}.png')
        page = await open_as('staff')
        await page.goto(BASE + f'/dex/{product}')
        await expect(page.get_by_label('Dex sync state')).to_contain_text('已发送 Sent')
        await expect(page.get_by_role('button', name='上传图片 Upload picture')).to_have_count(0)
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
    print('PASS: Dex sync: off with the first send and its deletes by name; turn-on refused plainly while the website '
          "half is missing and needing the typed count; on with a refused entry, its reason and Retry; the entry page's "
          'state with Upload picture for managers only; at 390 and 1440 px')
