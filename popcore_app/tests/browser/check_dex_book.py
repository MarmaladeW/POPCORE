"""图鉴 Dex in a real browser against the real Flask app and a disposable database (owner, 2026-11-12: "The Dex
itself lives on POPCORE"). A manager starts from the empty book, adds a company, an IP, a series, a product and
two designs, links the product to its POPCORE box, renames a design, moves one up and deletes one; staff read the
same pages with no write controls. Contract 1.3 (owner, 2026-11-13): a series with several IPs under the company;
the manager tags two of its designs with different IPs from the rows, and each shows on its own IP's page.
Widths 390 and 1440, no sideways overflow."""
import asyncio
import re
import subprocess
import sys
from contextlib import closing
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5199
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'dex-book'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_dex_book import DexBookTests
    fixture = DexBookTests()
    fixture.setUp()
    contexts = []

    def entries():
        with closing(fixture.connect()) as con:
            return {row['name']: dict(row) for row in con.execute('SELECT * FROM dex_entries WHERE deleted_at IS NULL')}

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

    async def add(page, button, name):
        await page.get_by_role('button', name=button).click()
        drawer = page.get_by_role('dialog')
        await drawer.get_by_label('名称 Name').fill(name)
        await drawer.get_by_role('button', name='保存 Save').click()
        await expect(page.get_by_role('heading', name=name, exact=True)).to_be_visible()
        await expect(page.get_by_role('dialog')).to_have_count(0)

    try:
        page = await open_as('manager')
        await page.goto(BASE + '/dex')
        empty = page.get_by_role('region', name='Empty book')
        await expect(empty.get_by_text('The book is empty')).to_be_visible()
        await fits(page, f'empty-{width}.png')

        await add(page, '添加公司 Add a company', 'POP MART')
        await add(page, '添加 Add IP', 'THE MONSTERS')
        await add(page, '添加 Add character', 'Labubu')
        await page.get_by_role('navigation', name='Breadcrumb').get_by_role('link', name='THE MONSTERS').click()
        await add(page, '添加 Add series', 'Have a Seat')
        # The product links its POPCORE box through the product search.
        await page.get_by_role('button', name='添加 Add product').click()
        drawer = page.get_by_role('dialog')
        await drawer.get_by_label('名称 Name').fill('Echo box')
        await drawer.get_by_role('switch').click()
        await drawer.get_by_role('combobox', name='POPCORE item').click()
        await drawer.get_by_role('combobox', name='POPCORE item').fill('PM0100')
        await page.locator('.ant-select-item-option', has_text='PM0100 · Echo PM0100').first.click()
        # Only boxes and ordinary items are offered: never a design or the sealed set.
        for sku in ('PM0100-01', 'PM0100-SET'):
            await expect(page.locator('.ant-select-item-option', has_text=sku)).to_have_count(0)
        await drawer.get_by_role('button', name='保存 Save').click()
        await expect(page.get_by_role('heading', name='Echo box', exact=True)).to_be_visible()
        card = page.get_by_role('region', name='Entry')
        await expect(card.get_by_text('PM0100 · Echo PM0100')).to_be_visible()
        await expect(card.get_by_text('是 Yes')).to_be_visible()
        product = entries()['Echo box']
        assert (product['product_id'], product['blind_box']) == (fixture.items['PM0100'], 1), product

        await add(page, '添加 Add design', 'Sun')
        await page.get_by_role('navigation', name='Breadcrumb').get_by_role('link', name='Echo box').click()
        await add(page, '添加 Add design', 'Moon')
        await page.get_by_role('navigation', name='Breadcrumb').get_by_role('link', name='Echo box').click()
        designs = page.get_by_role('region', name='Design here')
        await expect(designs.get_by_role('link')).to_have_count(2)

        # Moon moves up, before Sun.
        await designs.get_by_role('button', name='Move Moon up').click()
        await expect(designs.get_by_role('link').first).to_contain_text('Moon')
        book = entries()
        assert book['Moon']['sort_order'] < book['Sun']['sort_order'], book

        # Rename Sun.
        await designs.get_by_role('link', name='Sun').click()
        await page.get_by_role('button', name='编辑 Edit').click()
        drawer = page.get_by_role('dialog')
        await drawer.get_by_label('名称 Name').fill('Sun Star')
        await drawer.get_by_role('button', name='保存 Save').click()
        await expect(page.get_by_role('heading', name='Sun Star', exact=True)).to_be_visible()
        assert entries()['Sun Star']['version'] == 3  # added, moved down by the reorder, renamed

        # Delete Moon: the confirm says nothing sits under it.
        await page.get_by_role('navigation', name='Breadcrumb').get_by_role('link', name='Echo box').click()
        await designs.get_by_role('link', name='Moon').click()
        await page.get_by_role('button', name='删除 Delete').click()
        confirm = page.get_by_role('dialog')
        await expect(confirm.get_by_text('Nothing sits under it.')).to_be_visible()
        await confirm.get_by_role('button', name='删除 Delete').click()
        await expect(page.get_by_role('heading', name='Echo box', exact=True)).to_be_visible()
        await expect(designs.get_by_role('link')).to_have_count(1)
        assert 'Moon' not in entries()
        await fits(page, f'product-{width}.png')

        # The linked product cannot be deleted; its series says so.
        await page.get_by_role('navigation', name='Breadcrumb').get_by_role('link', name='Have a Seat').click()
        await page.get_by_role('button', name='删除 Delete').click()
        await expect(page.get_by_role('dialog').get_by_text('linked to a POPCORE item', exact=False)).to_be_visible()
        await page.get_by_role('dialog').get_by_role('button', name='OK').click()
        assert 'Have a Seat' in entries()

        await page.goto(BASE + '/dex')
        await expect(page.get_by_role('region', name='Companies').get_by_role('link', name='POP MART')).to_be_visible()
        await page.get_by_label('Search the book').fill('sun')
        await page.locator('.ant-select-item-option', has_text='Sun Star').click()
        await expect(page.get_by_role('heading', name='Sun Star', exact=True)).to_be_visible()
        await fits(page, f'design-{width}.png')

        # A series with several IPs stays under the company; each design names its IP from its row (no drawer).
        book = entries()
        company, monsters = book['POP MART']['id'], book['THE MONSTERS']['id']
        crybaby = fixture.add('ip', 'CRYBABY', company)
        anniversary = fixture.add('series', '15th Anniversary', company)
        box = fixture.add('product', 'Anniversary box', anniversary)
        for name in ('Labubu Star', 'Crybaby Moon'):
            fixture.add('design', name, box)
        await page.goto(BASE + f'/dex/{box}')
        designs = page.get_by_role('region', name='Design here')
        for design, ip in (('Labubu Star', 'THE MONSTERS'), ('Crybaby Moon', 'CRYBABY')):
            pick = designs.get_by_role('combobox', name=f'IP of {design}')
            await pick.click()
            await pick.fill(ip)  # searchable: the one match is active, Enter picks it
            await page.keyboard.press('Enter')
            await expect(designs.locator('.pc-dex-row', has_text=design).locator('.ant-select-selection-item')).to_have_text(ip)
        book = entries()
        assert (book['Labubu Star']['ip_id'], book['Crybaby Moon']['ip_id']) == (monsters, crybaby), book
        await expect(page.get_by_role('dialog')).to_have_count(0)
        await page.get_by_role('heading', name='Anniversary box', exact=True).first.click()  # close the last list
        await expect(page.locator('.ant-select-dropdown:visible')).to_have_count(0)
        await fits(page, f'mixed-product-{width}.png')
        # The editor offers the IP picker here, and not under an IP.
        await designs.get_by_role('link', name='Labubu Star').click()
        await expect(page.get_by_role('region', name='Entry').get_by_text('THE MONSTERS', exact=True)).to_be_visible()
        await page.get_by_role('button', name='编辑 Edit').click()
        drawer = page.get_by_role('dialog')
        await expect(drawer.locator('.ant-select-selection-item', has_text='THE MONSTERS')).to_be_visible()
        await page.keyboard.press('Escape')
        await expect(page.get_by_role('dialog')).to_have_count(0)
        await page.goto(BASE + f'/dex/{book["Sun Star"]["id"]}')
        await page.get_by_role('button', name='编辑 Edit').click()
        await expect(page.get_by_role('dialog').get_by_role('combobox', name='IP', exact=True)).to_have_count(0)
        await page.keyboard.press('Escape')
        # Each design shows on its own IP's page, with its path, and links to it.
        await page.goto(BASE + f'/dex/{monsters}')
        also = page.get_by_role('region', name='Also in')
        await expect(also.get_by_role('link')).to_have_count(1)
        await expect(also.get_by_role('link', name='Labubu Star')).to_contain_text('POP MART › 15th Anniversary › Anniversary box')
        await fits(page, f'mixed-ip-{width}.png')
        await also.get_by_role('link', name='Labubu Star').click()
        await expect(page.get_by_role('heading', name='Labubu Star', exact=True)).to_be_visible()
        await page.goto(BASE + f'/dex/{crybaby}')
        await expect(page.get_by_role('region', name='Also in').get_by_role('link')).to_have_text(
            [re.compile('Crybaby Moon')])

        # Staff read the book but see no write controls.
        page = await open_as('staff')
        await page.goto(BASE + '/dex')
        await expect(page.get_by_role('region', name='Companies').get_by_role('link', name='POP MART')).to_be_visible()
        await expect(page.get_by_role('button', name='添加 Add')).to_have_count(0)
        await page.goto(BASE + f'/dex/{entries()["Echo box"]["id"]}')
        await expect(page.get_by_role('heading', name='Echo box', exact=True)).to_be_visible()
        for name in ('编辑 Edit', '删除 Delete', '添加 Add design', 'Move Sun Star up', 'Move Sun Star down'):
            await expect(page.get_by_role('button', name=name)).to_have_count(0)
        await fits(page, f'staff-product-{width}.png')
        # Staff see each design's IP in its row, with no picker.
        await page.goto(BASE + f'/dex/{box}')
        designs = page.get_by_role('region', name='Design here')
        await expect(designs.get_by_text('IP THE MONSTERS')).to_be_visible()
        await expect(designs.get_by_role('combobox')).to_have_count(0)
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
    print('PASS: Dex book from empty: company, IP, series, product linked to its POPCORE box, designs added, '
          'renamed, reordered and deleted; a linked product kept; designs in a mixed series name their IPs from the rows '
          'and show on each IP\'s page; staff see no write controls; at 390 and 1440 px')
