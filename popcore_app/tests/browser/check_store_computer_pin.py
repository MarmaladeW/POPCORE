"""Name + PIN on the shared store computer in a real browser against the real Flask app, a disposable database and a
fake WooCommerce (owner, 2026-11-20: every action leaves a mark of who did it): a staff member sets their PIN from
我的 PIN; on the store computer's counter account, 拣好了 Ready asks 谁在操作？, a wrong PIN is refused, the right
one sends the same Ready once more, the website note and 动态 name that person, and a bar shows who is using the
computer until 结束 Done; nothing scrolls sideways, at 390 and 1440 px."""
import asyncio
import re
import subprocess
import sys
from contextlib import closing
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

from check_foundation import FRONTEND, HERE, ROOT

PORT = 5191
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'store-computer-pin'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_website_board import CounterAccountTests
    fixture = CounterAccountTests('test_a_viewer_without_store_operations_access_gets_nothing_from_the_board')
    fixture.setUp()
    contexts = []

    async def open_as(role):
        context = await browser.new_context(viewport={'width': width, 'height': 900})
        contexts.append(context)
        await context.add_init_script(
            f"window.__FOUNDATION_AUTH={{role:'{role.split(':')[0]}',token:'{role}'}};"
            f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.dt},code:'DT',name:'Downtown'}}));"
            "Object.defineProperty(Notification,'permission',{get:()=>'default'})")
        page = await context.new_page()

        async def flask_boundary(route):
            parsed = urlsplit(route.request.url)
            if parsed.hostname not in ('127.0.0.1', 'localhost'):
                await route.abort(); return
            if not parsed.path.startswith('/api/'):
                await route.continue_(); return
            headers = {'Authorization': f'Bearer {role}'}
            person = route.request.headers.get('x-popcore-person')
            if person:
                headers['X-Popcore-Person'] = person
            body = route.request.post_data_json if route.request.post_data else None
            response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                           method=route.request.method, headers=headers, json=body)
            await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
        await page.route('**/*', flask_boundary)
        return page

    try:
        # Amy sets her PIN from her own phone.
        phone = await open_as('staff')
        await phone.goto(BASE + '/')
        prompt = phone.get_by_role('note').filter(has_text='设置你的 4 位 PIN')
        await expect(prompt).to_be_visible()
        await prompt.get_by_role('button', name='设置 Set').click()
        await phone.get_by_label('新 PIN New PIN').fill('2580')
        await phone.get_by_label('再输一次 Again').fill('2580')
        await phone.get_by_role('button', name='保存 Save').click()
        await expect(phone.get_by_text('PIN 已设置 PIN saved')).to_be_visible()
        await expect(prompt).to_have_count(0)

        # The store computer: Ready asks who is doing it.
        page = await open_as('viewer:counter')
        await page.goto(BASE + '/web-orders')
        pick = page.get_by_role('region', name='Pickup · to pick')
        await pick.get_by_role('button', name='拣好了 Ready').click()
        await page.get_by_role('button', name='Ready', exact=True).click()
        picker = page.get_by_role('dialog')
        await expect(picker.get_by_text('谁在操作？Who is doing this?')).to_be_visible()
        await fits(page, f'who-{width}.png')
        await picker.get_by_role('listitem').filter(has_text='Amy').click()
        await expect(picker.get_by_text('Amy · 输入 PIN Type your PIN')).to_be_visible()
        for digit in '1111':
            await picker.get_by_role('button', name=digit, exact=True).click()
        await expect(picker.get_by_text('Wrong PIN.')).to_be_visible()
        await fits(page, f'pin-{width}.png')
        for digit in '2580':
            await picker.get_by_role('button', name=digit, exact=True).click()
        await expect(page.get_by_text('#701 is ready', exact=False)).to_be_visible()
        assert fixture.woo.notes[-1][1]['note'] == 'Ready in popcore.store by Amy', fixture.woo.notes
        with closing(fixture.connect()) as con:
            row = con.execute("""SELECT actor_name, via_sub FROM activity_log WHERE endpoint='website.web_orders_step'
                                 ORDER BY id DESC LIMIT 1""").fetchone()
        assert tuple(row) == ('Amy', 'auth0|counter'), tuple(row)
        bar = page.get_by_role('status').filter(has_text='正在操作')
        await expect(bar).to_contain_text('Amy')
        await fits(page, f'bar-{width}.png')
        await bar.get_by_role('button', name=re.compile('结束')).click()
        await expect(bar).to_have_count(0)
        # A PIN is a person's: the store computer's account is not offered 我的 PIN (owner, 2026-11-26).
        if width >= 768:
            await page.get_by_role('button', name='Account menu').click()
            await expect(page.get_by_role('menuitem', name='退出登录 Sign out')).to_be_visible()
        else:
            await page.goto(BASE + '/more')
            await expect(page.get_by_role('button', name='退出登录 Sign out')).to_be_visible()
        await expect(page.get_by_text('我的 PIN', exact=False)).to_have_count(0)
    finally:
        for context in contexts:
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
    print('PASS: name + PIN on the store computer: 我的 PIN from the phone, Ready asks who, a wrong PIN refused, the '
          'right one sends Ready again, the website note and 动态 name the person, the bar until Done; at 390 and 1440 px')
