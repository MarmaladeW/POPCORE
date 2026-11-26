"""移货 Move stock in a real browser against the real Flask routes and a disposable live store (owner, 2026-11-24:
one easy way to move goods): the home tile opens it with 楼上 → 楼下 picked; a whole set comes down as its boxes in
one Save; 撤销 puts it back; a move POPCORE is short for says so before saving; 不在了 asks why; nothing scrolls
sideways, at 390 and 1440 px."""
import asyncio
import subprocess
import sys
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

from check_foundation import FRONTEND, HERE, ROOT

PORT = 5199
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'move-stock'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_stock_moves import StockMoveTests
    fixture = StockMoveTests('test_the_store_offers_its_own_places_new_goods_and_gone')
    fixture.setUp()
    context = await browser.new_context(viewport={'width': width, 'height': 900})
    try:
        await context.add_init_script(
            "window.__FOUNDATION_AUTH={role:'staff',token:'staff'};"
            f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.dt},code:'DT',name:'Downtown'}}));")
        page = await context.new_page()

        async def flask_boundary(route):
            parsed = urlsplit(route.request.url)
            if parsed.hostname not in ('127.0.0.1', 'localhost'):
                await route.abort(); return
            if not parsed.path.startswith('/api/'):
                await route.continue_(); return
            headers = {'Authorization': 'Bearer staff'}
            if route.request.headers.get('idempotency-key'):
                headers['Idempotency-Key'] = route.request.headers['idempotency-key']
            body = route.request.post_data_json if route.request.post_data else None
            response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                           method=route.request.method, headers=headers, json=body)
            await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
        await page.route('**/*', flask_boundary)

        # The home tile opens 移货 with what most moves are already picked: 楼上 → 楼下.
        await page.goto(BASE + '/')
        await page.get_by_role('navigation', name='Store actions').get_by_role('link', name='移货', exact=False).click()
        await expect(page).to_have_url(BASE + '/move')
        source, target = page.get_by_role('radiogroup', name='从哪里'), page.get_by_role('radiogroup', name='到哪里')
        await expect(source.get_by_role('radio', name='楼上')).to_be_checked()
        await expect(target.get_by_role('radio', name='楼下')).to_be_checked()
        save = page.get_by_role('button', name='加要移的货')
        await expect(save).to_be_disabled()

        search = page.get_by_label('记账名、名字或编号 Name or code')
        await search.fill('Echo')
        await page.get_by_role('list', name='Products found').get_by_role('button').filter(has_not_text='整端').first.click()
        await page.get_by_label('Echo sets').fill('1')
        await expect(page.get_by_text('1 端 = 12 盒')).to_be_visible()
        await fits(page, f'move-{width}.png')
        await page.get_by_role('button', name='保存 · 楼上 → 楼下').click()
        done = page.get_by_role('status', name='Saved')
        await expect(done).to_contain_text('楼上 → 楼下')
        await expect(done).to_contain_text('Echo 1端')
        assert (fixture.at(fixture.set, 'upstairs'), fixture.at(fixture.box, 'floor')) == (1, 42)

        # 撤销 from today's list puts it back.
        today = page.get_by_role('list', name='Moves today')
        await expect(today.get_by_role('listitem')).to_have_count(1)
        await today.get_by_role('button', name='撤销').click()
        await page.get_by_role('tooltip').get_by_role('button', name='撤销').click()
        await expect(today).to_contain_text('已撤销 · Zimo')
        assert (fixture.at(fixture.set, 'upstairs'), fixture.at(fixture.box, 'floor')) == (2, 30)

        # More than POPCORE holds upstairs: it says so before saving, and the move still goes through.
        await search.fill('Echo')
        await page.get_by_role('list', name='Products found').get_by_role('button').filter(has_not_text='整端').first.click()
        await page.get_by_label('Echo sets').fill('3')
        await expect(page.get_by_text('电脑里楼上少 12，照样移过去')).to_be_visible()
        await fits(page, f'short-{width}.png')
        await page.get_by_role('button', name='保存 · 楼上 → 楼下').click()
        await expect(page.get_by_role('status', name='Saved')).to_contain_text('有空盘点一下')
        assert (fixture.at(fixture.set, 'upstairs'), fixture.at(fixture.box, 'floor')) == (0, 66)

        # 不在了 asks why; from the floor only boxes or pieces are asked.
        await source.get_by_role('radio', name='楼下').click()
        await target.get_by_role('radio', name='不在了').click()
        await search.fill('Bear')
        await page.get_by_role('list', name='Products found').get_by_role('button').first.click()
        await page.get_by_label('Bear 吊卡 pieces').fill('1')
        await expect(page.get_by_role('button', name='选原因')).to_be_disabled()
        # 娃娃机出奖 is offered only out of the claw machine (the server refuses it from anywhere else): not from 楼下.
        await expect(page.get_by_role('radiogroup', name='为什么').get_by_role('radio', name='娃娃机出奖')).to_have_count(0)
        await page.get_by_role('radiogroup', name='为什么').get_by_role('radio', name='坏了').click()
        await fits(page, f'gone-{width}.png')
        await page.get_by_role('button', name='保存 · 楼下 → 不在了').click()
        await expect(page.get_by_role('status', name='Saved')).to_contain_text('楼下 → 不在了')
        assert fixture.at(fixture.item, 'floor') == 2
        await expect(today.get_by_role('listitem').first).to_contain_text('坏了')
        await fits(page, f'today-{width}.png')
    finally:
        await context.close()
        fixture.doCleanups()
        fixture.tearDown()


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
    print('PASS: 移货 Move stock: the home tile opens it on 楼上 → 楼下, a set comes down as boxes in one Save, 撤销 puts it '
          'back, a short move says so and still arrives, 不在了 asks why; at 390 and 1440 px')
