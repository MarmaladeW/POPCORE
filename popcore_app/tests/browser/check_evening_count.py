"""晚盘 at a live store in a real browser against the real Flask routes and a disposable database (owner, 2026-11-20):
staff count each bestseller on the floor as WeChat writes it (sets and loose boxes), save, see ✅ or 🥲 with their
name, and copy the WeChat message; at 390 and 1440 px."""
import asyncio
import re
import subprocess
import sys
from contextlib import closing
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

from check_foundation import FRONTEND, HERE, ROOT

PORT = 5187
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'evening-count'


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_staff_rollout import LiveEveningCountTests
    fixture = LiveEveningCountTests('test_staff_count_sets_and_loose_and_see_whether_it_matches')
    fixture.setUp()
    context = await browser.new_context(viewport={'width': width, 'height': 900}, permissions=['clipboard-read', 'clipboard-write'])
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
            body = route.request.post_data_json if route.request.post_data else None
            response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                           method=route.request.method, headers={'Authorization': 'Bearer staff'}, json=body)
            await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
        await page.route('**/*', flask_boundary)

        await page.goto(BASE + '/stock/count')
        rows = page.get_by_role('list', name='Evening count').get_by_role('listitem')
        await expect(rows).to_have_count(2)
        await page.get_by_label('Echo sets').fill('2')
        await page.get_by_label('Echo loose boxes').fill('6')
        await page.get_by_label('Bear 吊卡 pieces').fill('2')
        await page.get_by_role('button', name='保存晚盘 Save').click()
        await expect(page.get_by_text('晚盘已保存，1 项不一样 🥲 Saved; 1 differ')).to_be_visible()
        await expect(rows.filter(has_text='Echo').get_by_text('✅ 对上 Matches')).to_be_visible()
        await expect(rows.filter(has_text='Bear').get_by_text('🥲 不一样 Differs')).to_be_visible()
        await expect(rows.filter(has_text='Echo')).to_contain_text('Zimo')
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path=OUT / f'evening-{width}.png', animations='disabled', full_page=True)
        await page.get_by_role('button', name='复制到微信 Copy for WeChat').click()
        await expect(page.get_by_text('已复制，去微信粘贴 Copied: paste it in WeChat')).to_be_visible()
        copied = await page.evaluate('navigator.clipboard.readText()')
        assert copied.endswith('DT 晚盘\nEcho 12*2➕6 ✅\nBear 吊卡 2 🥲'), copied
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
    print('PASS: 晚盘 at a live store: sets and loose typed, saved with ✅ and 🥲 and the counter named, copied for WeChat; at 390 and 1440 px')
