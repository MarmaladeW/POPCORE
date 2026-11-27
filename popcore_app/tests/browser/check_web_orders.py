"""网站订单 Web orders in a real browser against the real Flask app, a disposable database and a fake WooCommerce
(owner, 2026-11-19): staff see DT's web orders in their five columns, the ones that need a manager and those done
today; Ready moves a pickup on the website; the code box finds an order by its code and shows the customer's first
name, and Handed over completes it and clears the name; delivery cards show the name the parcel goes to and the name
box finds them by part of a name (owner, 2026-11-25); the board's data holds no pickup code and nothing personal but
those names; a delivery shows at the store it leaves from, Markham's included (owner, 2026-11-26);
viewers cannot open the page; a new order pops up at the board's next refresh and the notifications control is
there (owner, 2026-11-19)."""
import asyncio
import subprocess
import sys
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5198
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'web-orders'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_website_board import CONTACT, WebOrdersBoardTests
    from test_website_sync import _ago
    fixture = WebOrdersBoardTests()
    fixture.setUp()
    contexts, board_bodies = [], []

    async def open_as(role, token=None, store=None):
        token = token or role  # the server's sign-in: 'viewer:counter' is the viewer auth0|counter
        store_id, code, name = store or (fixture.dt, 'DT', 'Downtown')
        context = await browser.new_context(viewport={'width': width, 'height': 900})
        contexts.append(context)
        await context.add_init_script(
            f"window.__FOUNDATION_AUTH={{role:'{role}',token:'{role}'}};"
            f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{store_id},code:'{code}',name:'{name}'}}));"
            # Headless Chromium always answers "denied"; a browser nobody has asked yet answers "default".
            "Object.defineProperty(Notification,'permission',{get:()=>'default'})")
        page = await context.new_page()

        async def flask_boundary(route):
            parsed = urlsplit(route.request.url)
            if parsed.hostname not in ('127.0.0.1', 'localhost'):
                await route.abort(); return
            if not parsed.path.startswith('/api/'):
                await route.continue_(); return
            body = route.request.post_data_json if route.request.post_data else None
            response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                           method=route.request.method, headers={'Authorization': f'Bearer {token}'},
                                           json=body)
            if parsed.path == '/api/website/board':
                board_bodies.append(response.get_data(as_text=True))
            await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
        await page.route('**/*', flask_boundary)
        return page

    try:
        page = await open_as('staff')
        await page.clock.install()  # the board's 30-second refresh is moved on by hand below
        await page.goto(BASE + '/web-orders')
        # Notifications: this browser can take them, and nothing is turned on until someone taps.
        notify = page.get_by_role('region', name='Notifications')
        await expect(notify.get_by_role('button', name='在本设备开启 Notify me on this device')).to_be_visible()
        await expect(notify.get_by_role('switch')).to_be_checked()
        assert await page.evaluate("navigator.serviceWorker.register('/sw.js').then(r => r.unregister())")
        pick = page.get_by_role('region', name='Pickup · to pick')
        await expect(pick.get_by_text('#701')).to_be_visible()
        await expect(pick.get_by_text('Bear 记账名')).to_be_visible()
        await expect(pick.get_by_text('PM0200 · DT 楼下 Floor 1')).to_be_visible()
        ready = page.get_by_role('region', name='Pickup · ready, waiting for the customer')
        await expect(ready.get_by_text('已等 4 天 · Waiting 4 days')).to_be_visible()
        pack = page.get_by_role('region', name='Delivery · to pack')
        await expect(pack.get_by_text('送达 Fri Nov 21, 2–6 pm')).to_be_visible()
        await expect(pack.get_by_text('区域 Zone Downtown')).to_be_visible()
        booked = page.get_by_role('region', name='Delivery · booked, waiting for the courier')
        await expect(booked.get_by_text('FleetNow DO202611190000000001 · CONFIRMED')).to_be_visible()
        await expect(page.get_by_role('region', name='Delivery · out for delivery').get_by_text('#705')).to_be_visible()
        manager = page.get_by_role('region', name='Needs a manager')
        await expect(manager.get_by_text('库存不够', exact=False)).to_be_visible()
        await expect(manager.get_by_text('A special order waiting for stock', exact=False)).to_be_visible()
        # Only the pickup to pick has a button; nothing else on the board does.
        await expect(page.get_by_role('button', name='拣好了 Ready')).to_have_count(1)
        # Each delivery shows who it goes to (owner, 2026-11-25): the shipping name, else the billing name.
        await expect(pack.get_by_text('收件人 Ship to Zoë Ng')).to_be_visible()
        await expect(booked.get_by_text('收件人 Ship to Ana Lima')).to_be_visible()
        await expect(page.get_by_role('region', name='Delivery · out for delivery').get_by_text('Private Person')).to_be_visible()
        await expect(pick.get_by_text('收件人', exact=False)).to_have_count(0)
        # A delivery says the store it leaves from, as a pickup says its store.
        await expect(pack.get_by_text('配送 DT', exact=True)).to_be_visible()
        await expect(pick.get_by_text('自取 DT', exact=True)).to_be_visible()
        await fits(page, f'board-{width}.png')

        # The name box finds deliveries by part of a name, whatever the case and accents; pickups only by code.
        names = page.get_by_role('region', name='Delivery name')
        await names.get_by_label('收件人 Delivery name').fill('ZOE')
        found = names.get_by_role('list', name='Deliveries found')
        await expect(found.get_by_role('article')).to_have_count(1)
        await expect(found.get_by_text('#703')).to_be_visible()
        await expect(found.get_by_text('配送 · 待打包 · Delivery · to pack')).to_be_visible()
        await fits(page, f'name-{width}.png')
        await names.get_by_label('收件人 Delivery name').fill('lim')
        await expect(found.get_by_text('#704')).to_be_visible()
        await names.get_by_label('收件人 Delivery name').fill('Sammy')  # 702's collector: a pickup
        await expect(names.get_by_text('No delivery order on the board has this name', exact=False)).to_be_visible()
        await names.get_by_role('button', name='清除 Clear').click()
        await expect(names.get_by_label('收件人 Delivery name')).to_have_value('')
        await expect(names.get_by_role('status')).to_have_count(0)

        # Ready: the website moves it and emails the customer; the card moves over.
        await pick.get_by_role('button', name='拣好了 Ready').click()
        await page.get_by_role('button', name='Ready', exact=True).click()
        await expect(page.get_by_text('#701 is ready', exact=False)).to_be_visible()
        assert fixture.woo.orders[701]['status'] == 'ready-pickup'
        await expect(ready.get_by_text('#701')).to_be_visible()
        await expect(pick.get_by_text('没有 · None')).to_be_visible()

        # The code box: an unknown code says so; the right one shows the order and the first name.
        code = page.get_by_role('region', name='Pickup code')
        await code.get_by_label('取货码 Pickup code').fill('zzzzzz')
        await code.get_by_role('button', name='查找 Find').click()
        await expect(code.get_by_text('No pickup order has this code', exact=False)).to_be_visible()
        await code.get_by_label('取货码 Pickup code').fill('acd-346')
        await code.get_by_role('button', name='查找 Find').click()
        await expect(code.get_by_text('Private', exact=True)).to_be_visible()
        await expect(code.get_by_text('Sammy', exact=True)).to_be_visible()
        await fits(page, f'code-{width}.png')
        await code.get_by_role('button', name='已取货 Handed over').click()
        await page.get_by_role('button', name='Handed over', exact=True).click()
        await expect(page.get_by_text('#702 handed over', exact=False)).to_be_visible()
        assert fixture.woo.orders[702]['status'] == 'completed'
        await expect(page.get_by_text('Private', exact=True)).to_have_count(0)
        await expect(ready.get_by_text('#702')).to_have_count(0)
        await page.get_by_text('Done today').click()
        await expect(page.locator('.pc-web-done').get_by_text('#702')).to_be_visible()

        # A new paid order pops up on the board at its next refresh.
        fixture.woo.order(720, [(11, 1)], pickup='dt', modified=_ago(0))
        fixture.run_sync()
        await page.clock.fast_forward(31_000)
        popup = page.locator('.ant-notification-notice').filter(has_text='新网站订单 New web order #720')
        await expect(popup).to_be_visible()
        await expect(popup.get_by_text('自取 DT pickup · Echo × 1')).to_be_visible()
        await expect(pick.get_by_text('#720')).to_be_visible()
        await fits(page, f'new-order-{width}.png')

        for body in board_bodies:
            for value in (*CONTACT, 'Sammy', 'K7R4MX', 'ACD346', 'HJK777', 'MKP999'):
                assert value not in body, value

        # The store computer's account (owner, 2026-11-19): a 查看者 Viewer with DT store operations access and 库存查看.
        with fixture.connect() as con:
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|counter', ?)", (fixture.dt,))
            con.execute("INSERT INTO inventory_viewers(auth0_sub, store_id) VALUES ('auth0|counter', ?)", (fixture.dt,))
        page = await open_as('viewer', 'viewer:counter')
        # It opens on the orders to prepare (owner, 2026-11-24).
        await page.goto(BASE + '/')
        await expect(page).to_have_url(BASE + '/web-orders')
        await expect(page.get_by_role('region', name='Pickup · to pick').get_by_text('#720')).to_be_visible()
        await expect(page.get_by_role('button', name='拣好了 Ready')).to_have_count(1)
        if width > 600:  # the rail: 网站订单 and 库存, nothing that changes anything
            rail = page.get_by_role('navigation', name='Primary navigation')
            await expect(rail.get_by_role('link', name='网站订单 Web orders')).to_be_visible()
            await expect(rail.get_by_role('link', name='库存 Inventory')).to_be_visible()
            await expect(rail.get_by_role('link', name='买单 Checkout')).to_have_count(0)
            for gone in ('首页 Home', '排班 Schedule'):
                await expect(rail.get_by_role('link', name=gone)).to_have_count(0)
            for there in ('产品 Products', '图鉴 Dex', '可疑人员 Watch list'):
                await expect(rail.get_by_role('link', name=there)).to_be_visible()
            await rail.get_by_role('link', name='库存 Inventory').click()
        else:
            bar = page.get_by_role('navigation', name='Primary navigation')
            await expect(bar.get_by_role('link', name='排班 Schedule')).to_have_count(0)
            await expect(bar.get_by_role('link', name='产品 Products')).to_be_visible()
            await page.goto(BASE + '/stock')
        await expect(page.get_by_role('heading', name='库存', exact=False).first).to_be_visible()
        await expect(page.get_by_role('navigation', name='Inventory sections')).to_have_count(0)
        await expect(page.get_by_text("You don't have permission", exact=False)).to_have_count(0)
        await fits(page, f'counter-stock-{width}.png')
        # No shifts for the store computer.
        await page.goto(BASE + '/schedule')
        await expect(page.get_by_text('Access Denied', exact=True)).to_be_visible()

        # A viewer with no 店铺电脑 store ticked: home says so, and the board names the tick it needs.
        page = await open_as('viewer')
        await page.goto(BASE + '/')
        await expect(page.get_by_role('heading', name='店铺电脑', exact=True)).to_be_visible()
        await expect(page.get_by_text('Once an admin ticks 店铺电脑 Store computer', exact=False)).to_be_visible()
        await fits(page, f'counter-no-store-{width}.png')
        await page.goto(BASE + '/web-orders')
        await expect(page.get_by_text('DT 没有勾选 店铺电脑', exact=False)).to_be_visible()

        # Owner, 2026-11-26 (popcore-work#121): the courier collects from the store the order was filled from, so
        # Markham's board shows the deliveries Markham packs. DT has 7 Echo boxes left, MK opens with 6.
        mk, _ = fixture.open_mk(6)
        with fixture.connect() as con:
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|staff', ?)", (mk,))
        fixture.woo.order(730, [(11, 8)], modified=_ago(0))  # no store has 8: DT's 7 and 1 of MK's
        fixture.woo.order(731, [(11, 2)], modified=_ago(0), shipping={'first_name': 'Mei', 'last_name': 'Markham'})
        # Filled from MK, but booked with no store named (the website's switch is off): the courier comes to DT.
        fixture.woo.order(732, [(11, 1)], modified=_ago(0), status='dispatched',
                          meta=[('fleetnow_order_no', 'DO202611260000000001'), ('fleetnow_status', 'CONFIRMED')])
        fixture.run_sync()
        assert fixture.woo.fill_writes[-3:] == [(730, 'dt,mk'), (731, 'mk'), (732, 'mk')], fixture.woo.fill_writes
        page = await open_as('staff', store=(mk, 'MK', 'Markham'))
        await page.goto(BASE + '/web-orders')
        pack = page.get_by_role('region', name='Delivery · to pack')
        mine = pack.get_by_role('article', name='Order #731')
        await expect(mine.get_by_text('配送 MK', exact=True)).to_be_visible()
        await expect(mine.get_by_text('收件人 Ship to Mei Markham')).to_be_visible()
        await expect(mine.locator('.pc-web-flag')).to_have_count(0)  # nothing to move: no flag
        split = pack.get_by_role('article', name='Order #730')
        await expect(split.get_by_text('配送 DT + MK', exact=True)).to_be_visible()
        await expect(split.get_by_text('Taken from DT and MK: bring it to one store', exact=False)).to_be_visible()
        await expect(pack.get_by_text('#703')).to_have_count(0)  # DT's delivery stays on DT's board
        booked = page.get_by_role('region', name='Delivery · booked, waiting for the courier')
        await expect(booked.get_by_role('article', name='Order #732').get_by_text(
            '从 MK 拿 · Taken from MK: bring it to DT')).to_be_visible()
        # MK's board has deliveries now, so it has the name box.
        await expect(page.get_by_role('region', name='Delivery name')).to_be_visible()
        await fits(page, f'markham-{width}.png')
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
    print('PASS: web orders board columns, Ready, the code box with the first name, Handed over clearing it, '
          'delivery names and the name box, nothing personal but delivery names in the board data, a new order popping up, the notifications control and its service '
          'worker, a viewer counter account working the board and reading 库存 without tools, Markham\'s board with the '
          'deliveries it packs, at 390 and 1440 px')
