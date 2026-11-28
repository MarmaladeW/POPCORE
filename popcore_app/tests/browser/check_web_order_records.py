"""每日汇总 and 网站订单记录 in a real browser against the real Flask app, a disposable database and a fake WooCommerce
(owner, 2026-11-28, popcore-work#157: "we'd also need a clearer 汇总页面, 网站订单 also need to be recorded, their
order details"; 汇总 is "instore orders and website orders"). An admin's 每日汇总 leads with the day's in-store total
and its own web total, lists each in-store order and each web order of the store (number, kind and store, the items
as the customer bought them, where it stands, the amount; opened: payment and the rest), keeps the copy text folded
away, and leaves the store's other web orders off; 销售 → 网站订单 lists the month by day and finds an order by number;
a cancelled order stays listed but out of the totals; nothing personal reaches the page; a manager's own 汇总 has no
web orders and the records tab is the admin's."""
import asyncio
import json
import subprocess
import sys
from datetime import datetime, time, timezone
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5203
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'web-order-records'
WEBSITE_NAMES = {701: 'The Golden Gallop Series Figures - WEALTH DRUM (Secret)',
                 703: 'Finding Mokoko Series Figures - Move Bravely',
                 708: 'Sonny Angel Summer Vacation - Shark'}


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


def seed(fixture, today):
    """Three in-store orders today at DT (card, cash, a split), the web orders paid this morning in Toronto with their
    website names, one of them cancelled, and a manager on shift."""
    from website_sync import TORONTO
    morning = lambda hour, minute: datetime.combine(datetime.fromisoformat(today).date(), time(hour, minute), TORONTO) \
        .astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    from contextlib import closing
    with closing(fixture.connect()) as con:
        for reference, at, tenders, lines in (
                ('Q-0001', (10, 2), [('card', 2800)], [('Labubu 搪胶', 1)]),
                ('Q-0002', (10, 41), [('cash', 4200)], [('Smiski Cheer', 2), ('Hirono Echo', 1)]),
                ('Q-0003', (11, 15), [('cash', 1800), ('card', 2200)], [('SA Dream 吊卡', 2)])):
            payload = json.dumps({'lines': [{'product_name_snapshot': name, 'quantity': quantity} for name, quantity in lines]})
            order = con.execute("""INSERT INTO checkout_orders(store_id, business_date, reference, payload, created_by, status,
                                       created_at) VALUES (?, ?, ?, ?, 'auth0|staff', 'completed', ?)""",
                                (fixture.dt, today, reference, payload, morning(*at).replace('T', ' '))).lastrowid
            for tender, cents in tenders:
                con.execute("""INSERT INTO checkout_attempts(checkout_id, tender, amount_cents, assigned_to, status)
                               VALUES (?, ?, ?, 'auth0|staff', 'completed')""", (order, tender, cents))
        for number, at in ((701, (9, 5)), (703, (9, 40)), (704, (8, 10)), (705, (8, 30)), (706, (11, 50)), (707, (12, 10)),
                           (708, (12, 30)), (709, (12, 45))):
            row = con.execute('SELECT lines FROM website_orders WHERE wc_order_id=?', (number,)).fetchone()
            lines = json.loads(row[0])
            if number in WEBSITE_NAMES:
                lines[0]['name'] = WEBSITE_NAMES[number]
            con.execute('UPDATE website_orders SET paid_at=?, lines=? WHERE wc_order_id=?', (morning(*at), json.dumps(lines), number))
        con.execute("UPDATE website_orders SET wc_status='cancelled', state='cancelled' WHERE wc_order_id=706")
        con.execute("INSERT INTO employees(auth0_id, name) VALUES ('auth0|lead', 'Lead')")
        con.execute("""INSERT INTO shifts(employee_id, date, start_time, end_time, assigned_by, store_id)
                       VALUES ((SELECT id FROM employees WHERE auth0_id='auth0|lead'), ?, '09:00', '17:00', 'test', ?)""",
                    (today, fixture.dt))
        con.commit()


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_website_board import CONTACT, PRIVATE
    from test_web_order_records import WebOrderRecordsTests
    from checkout_access import business_date
    fixture = WebOrderRecordsTests()
    fixture.setUp()
    today = business_date()
    seed(fixture, today)
    contexts, bodies = [], []

    async def open_as(role, token):
        context = await browser.new_context(viewport={'width': width, 'height': 900})
        contexts.append(context)
        await context.add_init_script(
            f"window.__FOUNDATION_AUTH={{role:'{role}',token:'{role}'}};"
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
                                           method=route.request.method, headers={'Authorization': f'Bearer {token}'},
                                           json=body)
            if parsed.path in ('/api/store-events', '/api/website/records'):
                bodies.append(response.get_data(as_text=True))
            await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
        await page.route('**/*', flask_boundary)
        return page

    try:
        page = await open_as('admin', 'admin:staff')
        await page.goto(BASE + '/summary')
        store = page.get_by_role('region', name='In-store orders')
        web = page.get_by_role('region', name='Web orders')
        # In-store: the day's total large, made of 3 orders, cash and card; each order with its items and payment.
        await expect(store.get_by_text('$110.00', exact=True)).to_be_visible()
        await expect(store.get_by_text('3 单 · 现金 $60.00 · 刷卡 $50.00', exact=True)).to_be_visible()
        await expect(store.get_by_text('Smiski Cheer × 2 · Hirono Echo × 1')).to_be_visible()
        await expect(store.get_by_text('现金 $18.00 + 刷卡 $22.00')).to_be_visible()
        # Web: DT's orders paid today, their own total; MK's pickup is on MK's summary, the cancelled one counts nothing.
        await expect(web.get_by_text('Web · 不计入门店')).to_be_visible()
        await expect(web.get_by_text('6 单 · 自取 3 · 配送 3 · 1 单取消不计', exact=True)).to_be_visible()
        await expect(web.get_by_text('$271.20', exact=True)).to_be_visible()
        for number in ('#701', '#703', '#704', '#705', '#706', '#707', '#708'):
            await expect(web.get_by_text(number, exact=True)).to_be_visible()
        await expect(web.get_by_text('#709', exact=True)).to_have_count(0)
        await expect(web.get_by_text('#702', exact=True)).to_have_count(0)  # paid days ago
        order = web.locator('details', has_text='#701')
        await expect(order.locator('summary').get_by_text(WEBSITE_NAMES[701])).to_be_visible()
        await expect(order.get_by_text('自取 DT', exact=True)).to_be_visible()
        await expect(order.locator('summary').get_by_text('待拣货')).to_be_visible()
        await expect(web.locator('details', has_text='#705').locator('summary').get_by_text('派送中')).to_be_visible()
        await expect(web.locator('details', has_text='#708').locator('summary').get_by_text('已完成')).to_be_visible()
        await expect(web.locator('details', has_text='#706').locator('summary').get_by_text('已取消')).to_be_visible()
        await expect(web.locator('details', has_text='#707').locator('summary').get_by_text('待处理')).to_be_visible()
        await page.screenshot(path=OUT / f'summary-top-{width}.png', animations='disabled')
        # Opened: the line amounts, the name staff know it by, the payment and tax.
        await order.locator('summary').click()
        await expect(order.get_by_text('Bear 记账名 · PM0200')).to_be_visible()
        await expect(order.get_by_text('付款 Credit Card (Stripe) · 税 $5.20')).to_be_visible()
        waiting = web.locator('details', has_text='#707')
        await waiting.locator('summary').click()
        await expect(waiting.get_by_role('link', name='去处理')).to_have_attribute('href', '/website')
        # The copy text is folded away until asked for, and carries the web orders as their own line.
        copy_text = page.get_by_label('Summary text')
        await expect(copy_text).to_be_hidden()
        await page.get_by_text('汇总文字', exact=True).click()
        await expect(copy_text).to_be_visible()
        text = await copy_text.input_value()
        assert '网站订单（不计入门店）: 6 单，$271.20' in text and '#709' not in text, text
        await page.get_by_text('汇总文字', exact=True).click()
        await fits(page, f'summary-{width}.png')

        # 销售 → 网站订单: this month by day, the store's own, and a search across months.
        await page.get_by_role('link', name='全部记录').click()
        await expect(page).to_have_url(BASE + '/sales/web-orders')
        records = page.get_by_role('region', name='Web orders')
        await expect(page.get_by_role('navigation', name='Sales sections').get_by_role('link', name='网站订单', exact=False)) \
            .to_have_attribute('aria-current', 'page')
        await expect(records.get_by_text('#701', exact=True)).to_be_visible()
        await expect(records.get_by_text('#709', exact=True)).to_have_count(0)
        await fits(page, f'records-{width}.png')
        await page.get_by_label('Search web orders').fill('702')
        await expect(records.get_by_text('搜索结果')).to_be_visible()
        await expect(records.locator('details')).to_have_count(1)
        await expect(records.get_by_text('待取货')).to_be_visible()
        await page.get_by_label('Search web orders').fill('nothing like this')
        await expect(records.get_by_text('没有找到这个订单 · No web order matches.')).to_be_visible()
        await fits(page, f'records-search-{width}.png')
        for body in bodies:
            for value in (*CONTACT, *PRIVATE, 'Zoë', 'Lima', 'Sammy', 'K7R4MX', 'ACD346', 'HJK777', 'MKP999', 'pickup_code'):
                assert value not in body, value

        # A manager's own 汇总 has no web orders; the records tab is the admin's.
        page = await open_as('manager', 'manager:lead')
        await page.goto(BASE + '/summary')
        await expect(page.get_by_role('region', name='In-store orders')).to_be_visible()
        await expect(page.get_by_text('只有你自己的订单和记录', exact=False)).to_be_visible()
        await expect(page.get_by_role('region', name='Web orders')).to_have_count(0)
        await fits(page, f'manager-summary-{width}.png')
        await page.goto(BASE + '/sales/web-orders')
        await expect(page.get_by_role('region', name='Web orders')).to_have_count(0)
        await expect(page.get_by_role('link', name='回首页 Home')).to_be_visible()
    except Exception:
        await page.screenshot(path=OUT / f'failed-{width}.png', full_page=True)
        raise
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
    print('PASS: 每日汇总 in-store and web orders as their own lines with details, the folded copy text, 网站订单 records '
          'by month and search, cancelled orders out of totals, nothing personal, a manager\'s own 汇总 without web '
          'orders and the records closed to managers, at 390 and 1440 px')
