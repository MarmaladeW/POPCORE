"""库存查看 Inventory view in a real browser against the real Flask app and a disposable database
(owner, 2026-11-01): a staff member nobody picked keeps 晚盘, 补货 and the catalog but sees no stock
numbers and no 库存 tab; an admin ticks them for DT in Users and the numbers and 库存 appear; managers
are always shown as seeing stock."""
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
OUT = ROOT / '.local' / 'inventory-visibility'
USERS = [{'id': 'auth0|admin', 'username': 'Admin', 'role': 'admin', 'is_active': 1},
         {'id': 'auth0|manager', 'username': 'Manager', 'role': 'manager', 'is_active': 1},
         {'id': 'auth0|staff', 'username': 'Staff', 'role': 'staff', 'is_active': 1}]


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def open_as(browser, fixture, role, width):
    context = await browser.new_context(viewport={'width': width, 'height': 900})
    await context.add_init_script(
        f"window.__FOUNDATION_AUTH={{role:'{role}',token:'{role}'}};"
        f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.store_id},code:'DT',name:'Downtown'}}))")
    page = await context.new_page()

    async def flask_boundary(route):
        parsed = urlsplit(route.request.url)
        if parsed.hostname not in ('127.0.0.1', 'localhost'):
            await route.abort(); return
        if not parsed.path.startswith('/api/'):
            await route.continue_(); return
        if parsed.path == '/api/users':  # Auth0's user list; the grants themselves are real
            await route.fulfill(json=USERS); return
        headers = fixture.headers(role)
        if route.request.headers.get('idempotency-key'):
            headers['Idempotency-Key'] = route.request.headers['idempotency-key']
        body = route.request.post_data_json if route.request.post_data else None
        response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                       method=route.request.method, headers=headers, json=body)
        await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
    await page.route('**/*', flask_boundary)
    return context, page


async def product_stock(page, phone, name):
    """The 库存 figure shown for a product in the catalog."""
    holder = page.locator('.pc-prod-rows li') if phone else page.get_by_role('row')
    return holder.filter(has_text=name).locator('.pc-prod-stock')


async def unpicked(browser, fixture, width, session):
    phone = width < 768
    context, page = await open_as(browser, fixture, 'staff', width)
    try:
        await page.goto(BASE + '/')
        actions = page.get_by_role('navigation', name='Store actions')
        await expect(actions.locator('a[href="/stock/count"]')).to_be_visible()
        await expect(actions.locator('a[href="/restock"]')).to_be_visible()
        await expect(actions.locator('a[href="/stock"], a[href="/incoming"]')).to_have_count(0)
        if not phone:
            rail = page.locator('.pc-sidebar-menu')
            await expect(rail.locator('a[href="/stock/count"]')).to_be_visible()
            await expect(rail.locator('a[href="/stock"], a[href="/incoming"]')).to_have_count(0)
        await fits(page, f'unpicked-home-{width}.png')

        for path in ('/stock', '/stock/products', '/count', '/incoming', '/goods/receiving', '/stock/claw'):
            await page.goto(BASE + path)
            await expect(page.get_by_text('库存查看未开启 · Inventory view is off', exact=True)).to_be_visible()
        notice = page.get_by_role('main')
        await expect(notice.get_by_role('link', name='晚盘 Evening count', exact=True)).to_have_attribute('href', '/stock/count')
        await expect(notice.get_by_role('link', name='补货 Restock', exact=True)).to_have_attribute('href', '/restock')
        await fits(page, f'unpicked-hidden-{width}.png')

        await page.goto(BASE + '/stock/count')
        await expect(page.get_by_text('库存查看未开启 · Inventory view is off', exact=True)).to_have_count(0)
        await expect(page.get_by_role('tab', name=re.compile('今晚清单'))).to_be_visible()

        await page.goto(BASE + '/products')
        await expect(await product_stock(page, phone, 'Test Product')).to_have_text(re.compile('^有货'))
        await expect(await product_stock(page, phone, 'Empty Product')).to_have_text(re.compile('^缺货'))
        await fits(page, f'unpicked-products-{width}.png')
        await page.goto(BASE + '/products/series')
        await expect(page.get_by_text('库存查看 Inventory view is off for this store. Ask an admin to turn it on in Users.',
                                      exact=True)).to_be_visible()

        await page.goto(BASE + f'/restock?session_id={session}')
        modal = page.get_by_role('dialog').filter(has_text='补货单 #')
        await modal.get_by_placeholder('搜索系列 / 记账名 / SKU / 款式').fill('Product')
        series = modal.get_by_role('list', name='系列 Series').get_by_role('listitem')
        await expect(series.filter(has_text='Test Product').get_by_text('楼上有货', exact=True)).to_be_visible()
        await expect(series.filter(has_text='Empty Product').get_by_text('楼上缺货', exact=True)).to_be_visible()
        await fits(page, f'unpicked-restock-{width}.png')
    finally:
        await context.close()


async def admin_picks(browser, fixture, width, granted):
    context, page = await open_as(browser, fixture, 'admin', width)
    try:
        await page.goto(BASE + '/users')
        staff = page.get_by_role('checkbox', name='Staff: DT inventory view', exact=True)
        await expect(page.get_by_role('checkbox', name='Manager: DT inventory view', exact=True)).to_have_count(0)
        await expect(page.get_by_text('总是可见 Always', exact=True).first).to_be_visible()
        await expect(staff).to_be_checked(checked=not granted)
        await staff.click()
        await expect(staff).to_be_checked(checked=granted)
        await expect(page.get_by_text(f"Inventory view {'granted' if granted else 'revoked'}", exact=True)).to_be_visible()
        await fits(page, f"users-{'grant' if granted else 'revoke'}-{width}.png")
    finally:
        await context.close()
    with closing(fixture.connect()) as con:
        rows = con.execute("SELECT store_id FROM inventory_viewers WHERE auth0_sub='auth0|staff'").fetchall()
    assert [row[0] for row in rows] == ([fixture.store_id] if granted else []), rows


async def picked(browser, fixture, width):
    phone = width < 768
    context, page = await open_as(browser, fixture, 'staff', width)
    try:
        await page.goto(BASE + '/')
        actions = page.get_by_role('navigation', name='Store actions')
        await expect(actions.locator('a[href="/stock"]')).to_be_visible()
        await expect(actions.locator('a[href="/incoming"]')).to_be_visible()
        await page.goto(BASE + '/stock')
        await expect(page.get_by_text('库存查看未开启 · Inventory view is off', exact=True)).to_have_count(0)
        await expect(page.get_by_role('navigation', name='Inventory tools')).to_be_visible()
        await page.goto(BASE + '/products')
        await expect(await product_stock(page, phone, 'Test Product')).to_have_text('12')
        await expect(await product_stock(page, phone, 'Empty Product')).to_have_text('0')
        await fits(page, f'picked-products-{width}.png')
    finally:
        await context.close()


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from support import IsolatedApiCase
    fixture = IsolatedApiCase()
    fixture.setUp()
    try:
        with closing(fixture.connect()) as con:
            for subject in ('auth0|staff', 'auth0|admin'):  # 门店操作权限 at DT, so only 库存查看 differs
                con.execute('INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)', (subject, fixture.store_id))
            empty = con.execute("""INSERT INTO products(sku, name_cn_en, jizhanming, product_type)
                                   VALUES ('EMPTY-1', 'Empty Product', 'Empty Product', 'ordinary')""").lastrowid
            con.execute('INSERT INTO stock(product_id, store_id, upstairs_qty, instore_qty, claw_qty) VALUES (?, ?, 0, 0, 0)',
                        (empty, fixture.store_id))
            con.commit()
        session = fixture.client.post('/api/restock/sessions', headers=fixture.headers('staff'),
                                      json={'store_code': 'DT'}).get_json()['id']
        await unpicked(browser, fixture, width, session)
        await admin_picks(browser, fixture, width, granted=True)
        await picked(browser, fixture, width)
        await admin_picks(browser, fixture, width, granted=False)
    finally:
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
    print('PASS: unpicked staff keep 晚盘, 补货 and the catalog without stock numbers; an admin picks them '
          'per store in Users and 库存 and the numbers appear; managers always see stock; at 390 and 1440.')
