"""图鉴 Dex step 2 in a real browser against the real Flask app and a disposable database (owner, 2026-11-12: "The
Dex itself lives on POPCORE"). A small made-up book is loaded; the Dex home shows how many POPCORE items are not in
the book yet; a manager puts an item in the book under a series and a design under its box's book product, and is
told to put a design's box in first when it is not linked; staff read the list with no write controls. Widths 390
and 1440, no sideways overflow."""
import asyncio
import subprocess
import sys
from contextlib import closing
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5199
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'dex-unlinked'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_dex_load import DexLoadTests
    fixture = DexLoadTests()
    fixture.setUp()
    fixture.load()
    contexts = []

    def linked(sku):
        with closing(fixture.connect()) as con:
            row = con.execute("""SELECT e.kind, e.name, e.blind_box, (SELECT name FROM dex_entries x WHERE x.id=e.parent_id) AS parent
                                 FROM dex_entries e JOIN products p ON p.id=e.product_id
                                 WHERE p.sku=? AND e.deleted_at IS NULL""", (sku,)).fetchone()
            return dict(row) if row else None

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
        page = await open_as('manager')
        await page.goto(BASE + '/dex')
        card = page.get_by_role('link', name='Not in the book yet')
        await expect(card).to_contain_text('6')
        await fits(page, f'home-{width}.png')
        await card.click()
        listing = page.get_by_role('region', name='Items not in the book')
        await expect(listing.get_by_text('PM0200 · Have a Seat PM0200')).to_be_visible()
        await expect(listing.get_by_text('PM0100-SET')).to_have_count(0)  # never a sealed set
        await fits(page, f'list-{width}.png')

        # A close name is suggested; 连接 Link links the item to that book entry (never done by itself).
        # It shows the suggested entry's full name and its IP tag.
        suggested = listing.locator('.pc-dex-suggest-line', has_text='Suggested: THE MONSTERS Fun Park Vinyl Figure Blind Box · 100/100')
        await expect(suggested).to_be_visible()
        await expect(suggested.locator('.pc-dex-ip')).to_have_text('IP THE MONSTERS')
        assert linked('PM0500') is None
        await listing.get_by_role('button', name='Link PM0500 to THE MONSTERS Fun Park Vinyl Figure Blind Box').click()
        await expect(listing.get_by_text('THE MONSTERS Fun Park Vinyl Figure', exact=False)).to_have_count(0)
        assert linked('PM0500') == {'kind': 'product', 'name': 'Vinyl Figure Blind Box', 'blind_box': 1,
                                    'parent': 'Fun Park'}, linked('PM0500')

        # An ordinary item goes under a series, found by search.
        await listing.get_by_role('button', name='Put PM0200 in the book').click()
        dialog = page.get_by_role('dialog')
        await expect(dialog.get_by_label('名称 Name')).to_have_value('Have a Seat PM0200')
        await dialog.get_by_role('combobox', name='Goes under').fill('Have a')
        await page.locator('.ant-select-item-option', has_text='Have a Seat · 系列 Series').first.click()
        await dialog.get_by_role('button', name='放入 Put in').click()
        await expect(page.get_by_role('dialog')).to_have_count(0)
        await expect(listing.get_by_text('PM0200 · Have a Seat PM0200')).to_have_count(0)
        assert linked('PM0200') == {'kind': 'product', 'name': 'Have a Seat PM0200', 'blind_box': 0,
                                    'parent': 'Have a Seat'}, linked('PM0200')

        # A design goes under the book product its box is linked to, named by its design name.
        await listing.get_by_role('button', name='Put PM0100-03 in the book').click()
        dialog = page.get_by_role('dialog')
        await expect(dialog.get_by_text('A book design under')).to_be_visible()
        await expect(dialog.get_by_label('名称 Name')).to_have_value('Stars')
        await fits(page, f'design-{width}.png')
        await dialog.get_by_role('button', name='放入 Put in').click()
        await expect(page.get_by_role('dialog')).to_have_count(0)
        assert linked('PM0100-03') == {'kind': 'design', 'name': 'Stars', 'blind_box': None,
                                       'parent': 'Vinyl Figure Blind Box'}, linked('PM0100-03')

        # A design whose box is not in the book: put the box in first.
        await listing.get_by_role('button', name='Put PM0400 in the book').click()
        dialog = page.get_by_role('dialog')
        await expect(dialog.get_by_text('Put its box in first')).to_be_visible()
        await expect(dialog.get_by_role('button', name='放入 Put in')).to_be_disabled()
        await dialog.get_by_role('button', name='取消 Cancel').click()
        assert linked('PM0400') is None

        await page.get_by_label('Search items not in the book').fill('PM0300')
        await expect(listing.locator('.pc-dex-row')).to_have_count(1)
        await fits(page, f'search-{width}.png')

        # Staff read the list but see no write controls.
        page = await open_as('staff')
        await page.goto(BASE + '/dex/unlinked')
        listing = page.get_by_role('region', name='Items not in the book')
        await expect(listing.get_by_text('PM0300 · Have a Seat PM0300')).to_be_visible()
        await expect(page.get_by_role('button', name='放入图鉴 Put in the book')).to_have_count(0)
        await fits(page, f'staff-list-{width}.png')
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
    print('PASS: Dex not-in-the-book list: count on the Dex home, an item put under a series, a design under its '
          "box's book product, a design with no box in the book refused plainly, a suggested entry linked, search; staff see no write controls; "
          'at 390 and 1440 px')
