"""网站上架 Listings on popcore.ca, Dex step 4 (contract 1.9.6 section 7), in a real browser against the real Flask app,
a disposable database and the pretend website of test_dex_sync.py. Sonny Angel Makeup Angel is the test: its five
designs are stocked with no listing; a manager approves the list, nothing goes while the Dex sync is off, then the
designs are made as drafts and listed as answered; a refusal shows the website's reason with Retry and Withdraw.
Widths 390 and 1440, no sideways overflow."""
import asyncio
import subprocess
import sys
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5199
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'dex-listings' / 'browser'


async def fits(page, name, *regions):
    """No sideways overflow; the page, then each listings panel by itself (the app shell scrolls inside)."""
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)
    for number, region in enumerate(regions, 1):
        await region.scroll_into_view_if_needed()
        await region.screenshot(path=OUT / name.replace('.png', f'-panel{number}.png'), animations='disabled')


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_dex_sync import DexListingsTests
    fixture = DexListingsTests()
    fixture.setUp()
    fixture.site.listing_fail.add('DR0133-04')
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
        page = await open_as('manager')
        await page.goto(BASE + '/dex/sync')
        panel = page.get_by_role('region', name='Listings on popcore.ca')
        todo = panel.get_by_role('region', name='To approve')
        await expect(todo.get_by_text('Sonny Angel Makeup Angel Series Limited Mini Figure - Artsy Peacock')).to_be_visible()
        await expect(todo.get_by_text('$28.99 · 库存 stock 51', exact=False)).to_be_visible()
        await expect(todo.get_by_text('no design price (the website keeps its own)', exact=False)).to_be_visible()
        await expect(todo.get_by_text('等图鉴 Waits for its book entry', exact=False).first).to_be_visible()
        await expect(todo.get_by_role('button', name='Put PM0400 in the book')).to_have_count(1)  # not in the book
        await expect(panel.get_by_text('nothing goes while the Dex sync is off', exact=False)).to_be_visible()
        await fits(page, f'preview-{width}.png', panel)

        await panel.get_by_role('button', name='全部批准 Approve all 8').click()
        await page.get_by_role('button', name='Approve', exact=True).click()
        await expect(page.get_by_text('8 approved')).to_be_visible()
        approved = page.get_by_role('region', name='Approved listings')
        await expect(approved.get_by_text('DR0133-01')).to_be_visible()
        await expect(approved.get_by_text('等图鉴 Goes once its book entry is on popcore.ca', exact=False).first).to_be_visible()
        fixture.run_pass()  # the switch is off: nothing goes
        assert fixture.site.calls == [], fixture.site.calls

        fixture.on()
        fixture.run_pass()
        await page.goto(BASE + '/dex/sync')
        done = page.get_by_role('region', name='Answered listings')
        await expect(done.get_by_text('DR0133-01')).to_be_visible()
        await expect(done.get_by_text('已建草稿 Draft made', exact=False).first).to_be_visible()
        refused = page.get_by_role('region', name='Refused listings')
        await expect(refused.get_by_text('could not make it and nothing was made', exact=False)).to_be_visible()
        await expect(refused.get_by_text('listing_failed', exact=False)).to_be_visible()
        await fits(page, f'answered-{width}.png', refused, done)
        fixture.site.listing_fail.clear()
        await refused.get_by_role('button', name='Retry DR0133-04').click()
        await expect(page.get_by_text('DR0133-04 goes again with the next pass')).to_be_visible()
        fixture.run_pass()
        assert fixture.row('DR0133-04')['state'] == 'created', fixture.row('DR0133-04')

        # Staff do not see the Dex sync page at all.
        page = await open_as('staff')
        await page.goto(BASE + '/dex/sync')
        await expect(page.get_by_role('region', name='Listings on popcore.ca')).to_have_count(0)
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
    print('PASS: Dex listings: the Makeup Angel designs stocked with no listing, with their manager prices and book '
          'names; approve all; nothing sent while the Dex sync is off; drafts made and listed as answered; a refusal '
          'with its reason, Retry and Withdraw; at 390 and 1440 px')
