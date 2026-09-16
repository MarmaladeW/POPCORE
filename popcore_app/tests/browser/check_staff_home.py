"""Staff entrance and event recording, isolated synthetic API responses only."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

from playwright.async_api import async_playwright, expect
from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server
from check_attendance import fixture
from check_checkout_focus import checkout

OUT = Path(__file__).resolve().parents[3] / '.local/staff-home/browser'


async def actual_event_api(browser):
    """Exercise the real UI -> Flask -> SQLite cash boundary with disposable data."""
    from contextlib import closing
    from threading import Thread
    from urllib.parse import urlsplit
    from werkzeug.serving import make_server
    sys.path.insert(0, str(HERE.parent))
    from test_store_events import StoreEventsTests
    from closing_operations import cash_summary
    fixture_case = StoreEventsTests()
    fixture_case.setUp()
    server = make_server('127.0.0.1', 5058, fixture_case.app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = OUT.parent / 'actual-vite.mjs'
    config.write_text(f"import config from {json.dumps(str(HERE/'vite.config.mjs'))}; export default {{...config,server:{{...config.server,port:5178,proxy:{{'/api':'http://127.0.0.1:5058'}}}}}}")
    vite = subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--config', str(config)], cwd=FRONTEND, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                _, writer = await asyncio.open_connection('127.0.0.1', 5178)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(.1)
        else:
            raise RuntimeError('Actual API browser server did not start')
        context = await browser.new_context(viewport={'width': 390, 'height': 844})
        await context.add_init_script("window.__FOUNDATION_AUTH={role:'staff',token:'staff'};localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown'}))")
        page = await context.new_page()
        async def local_only(route):
            if urlsplit(route.request.url).hostname in ('127.0.0.1', 'localhost'):
                await route.continue_()
            else:
                await route.abort()
        await page.route('**/*', local_only)
        await page.goto('http://127.0.0.1:5178/claw')
        await page.get_by_role('button', name='换现金 / Cash exchange', exact=True).click()
        await page.get_by_role('combobox', name='Payment received by').click()
        await page.get_by_title('Card', exact=True).click()
        await page.get_by_role('textbox', name='Cash exchanged (CAD)').fill('20.00')
        await page.get_by_role('textbox', name='Payment reference / Note').fill('LOCAL-TEST-ONLY')
        await page.get_by_role('checkbox', name='I received this digital payment and handed over the same amount in cash.').check()
        await page.get_by_role('button', name='Save record', exact=True).click()
        await expect(page).to_have_url('http://127.0.0.1:5178/')
        await expect(page.get_by_role('region', name='Recent records')).to_contain_text('$20.00')
        with closing(fixture_case.connect()) as con:
            assert con.execute('SELECT COUNT(*) FROM store_events').fetchone()[0] == 1
            assert con.execute('SELECT SUM(amount_cents) FROM cash_events').fetchone()[0] == 2000
            assert con.execute('SELECT COUNT(*) FROM sale_documents').fetchone()[0] == 0
            assert cash_summary(con, fixture_case.store_id, fixture_case.today, 0)['expected_drawer_cents'] == 63000
        await page.get_by_role('navigation', name='Store actions').get_by_role('link', name='汇总', exact=False).click()
        await expect(page.get_by_label('Summary text')).to_contain_text('LOCAL-TEST-ONLY')
        await page.screenshot(path=OUT / 'actual-summary-390.png', full_page=True)
        await context.close()
    finally:
        vite.terminate()
        vite.wait(timeout=10)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        fixture_case.tearDown()
        fixture_case.doCleanups()


async def flow(browser, width):
    state = {'day': '2026-09-16', 'events': []}
    original = fixture(state)

    def api(path, query, request):
        if path == '/api/store-events':
            if request.method == 'POST':
                body = request.post_data_json
                assert body['store_id'] == 1
                event = dict(body, id=len(state['events'])+1, actor_name='Cashier', created_at='2026-09-16T14:00:00Z',
                             business_date=state['day'], product_name='Test Product')
                state['events'].append(event)
                return event
            return dict(business_date=state['day'], scope='personal', events=state['events'],
                        checkouts=[], receipts=[], transfers=[], summary_text='2026.9.16 DT\n娃娃机：\nTest Product*1')
        if path == '/api/products/search':
            return [dict(id=1, sku='TEST-1', jizhanming='Test Product', stock_unit='piece')]
        if path == '/api/checkouts/access':
            return dict(business_date=state['day'], role='staff',
                        live_stores=[dict(id=1, code='DT', name='Downtown')], history_stores=[])
        if path == '/api/checkouts':
            return dict(orders=[checkout()], staff=[], next_before_id=None, clover=dict(connected=False))
        if path == '/api/checkouts/1':
            return checkout()
        return original(path, query, request)

    state['api_payload'] = api
    context, page = await context_with_api(browser, state, viewport={'width': width, 'height': 844}, auth={'role': 'staff'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store', JSON.stringify({id:1,code:'DT',name:'Downtown'}))")
    await page.clock.install(time=datetime(2026, 9, 16, 14, tzinfo=timezone.utc))
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        await page.goto(BASE + '/')
        actions = page.get_by_role('navigation', name='Store actions')
        await expect(actions.get_by_role('link')).to_have_count(4)
        for name in ['买单', '入店', '娃娃机', '汇总']:
            await expect(actions.get_by_role('link', name=name, exact=False)).to_be_visible()
        await expect(page.get_by_role('button', name='Punch in', exact=True)).to_be_visible()
        await page.get_by_role('button', name='Punch in', exact=True).click()
        await expect(page.get_by_role('region', name="Today's attendance")).to_have_count(0)
        await page.screenshot(path=OUT / f'home-{width}.png', full_page=True)
        await actions.get_by_role('link', name='买单', exact=False).click()
        await expect(page.get_by_role('heading', name='Checkout', exact=True)).to_be_visible()
        await page.get_by_role('button', name='Register 1', exact=False).click()
        await expect(page.get_by_label('Customer pays')).to_be_visible()
        await page.get_by_role('link', name='Home', exact=True).click()
        await actions.get_by_role('link', name='入店', exact=False).click()
        await expect(page.get_by_role('link', name='收货 / Receive shipment', exact=False)).to_be_visible()
        await expect(page.get_by_role('link', name='调入 / Transfer goods', exact=False)).to_be_visible()
        await page.get_by_role('link', name='Home', exact=True).click()
        await actions.get_by_role('link', name='娃娃机', exact=False).click()
        await page.get_by_role('button', name='出奖 / Prize won', exact=True).click()
        await page.get_by_role('combobox', name='Product').fill('Test')
        await page.get_by_title('TEST-1 · Test Product', exact=True).click()
        await page.get_by_role('spinbutton', name='Quantity').fill('1')
        state['status_for_path'] = {'/api/store-events': (503, {'error': 'Synthetic connection failure'})}
        await page.get_by_role('button', name='Save record', exact=True).click()
        await expect(page.get_by_role('button', name='Retry', exact=True)).to_be_visible()
        state['status_for_path'] = {}
        await page.get_by_role('button', name='Retry', exact=True).click()
        await expect(page).to_have_url(BASE + '/')
        await expect(page.get_by_role('status').filter(has_text='Record saved')).to_be_visible()
        await expect(page.get_by_role('region', name='Recent records')).to_contain_text('Test Product')
        posts = [r for r in state['requests'] if r['path'] == '/api/store-events' and r['method'] == 'POST']
        assert len(posts) == 2 and posts[0]['idempotency_key'] == posts[1]['idempotency_key']
        assert posts[0]['post_data'] == posts[1]['post_data']
        await actions.get_by_role('link', name='汇总', exact=False).click()
        await expect(page.get_by_label('Summary text')).to_have_value('2026.9.16 DT\n娃娃机：\nTest Product*1')
        await expect(page.get_by_role('link', name='Import historical summaries', exact=True)).to_have_count(0)
        await page.screenshot(path=OUT / f'summary-{width}.png', full_page=True)
        await page.get_by_role('link', name='Home', exact=True).click()
        await actions.get_by_role('link', name='娃娃机', exact=False).click()
        await page.get_by_role('button', name='换现金 / Cash exchange', exact=True).click()
        await page.get_by_role('combobox', name='Payment received by').click()
        await page.get_by_title('E-transfer', exact=True).click()
        await page.get_by_role('textbox', name='Cash exchanged (CAD)').fill('20.00')
        await page.get_by_role('textbox', name='Payment reference / Note').fill('Transfer received, cash handed over')
        await page.get_by_role('button', name='Save record', exact=True).click()
        await expect(page.get_by_text('Confirm the payment received and cash handed over.', exact=True)).to_be_visible()
        await page.get_by_role('checkbox', name='I received this digital payment and handed over the same amount in cash.').check()
        await page.screenshot(path=OUT / f'cash-exchange-{width}.png', full_page=True)
        await page.get_by_role('button', name='Save record', exact=True).click()
        await expect(page).to_have_url(BASE + '/')
        last = [r for r in state['requests'] if r['path'] == '/api/store-events' and r['method'] == 'POST'][-1]['post_data']
        assert last['amount_cents'] == 2000 and last['tender'] == 'e_transfer' and last['kind'] == 'cash_exchange'
        assert 'product_id' not in last and 'quantity' not in last
        await actions.get_by_role('link', name='入店', exact=False).click()
        await page.get_by_role('link', name='入 display / Display arrival', exact=False).click()
        await expect(page.get_by_text('This adds a record to the daily summary. Stock balances are unchanged.', exact=True)).to_be_visible()
        await page.get_by_role('combobox', name='Product').fill('Test')
        await page.get_by_title('TEST-1 · Test Product', exact=True).click()
        await page.get_by_role('spinbutton', name='Quantity').fill('2')
        await page.get_by_role('button', name='Save record', exact=True).click()
        await expect(page).to_have_url(BASE + '/')
        last = [r for r in state['requests'] if r['path'] == '/api/store-events' and r['method'] == 'POST'][-1]['post_data']
        assert last['kind'] == 'display_in' and last['quantity'] == 2
        await page.get_by_label('Operations store').select_option('ALL')
        await actions.get_by_role('link', name='娃娃机', exact=False).click()
        await expect(page.get_by_role('button', name='Save record', exact=True)).to_have_count(0)

        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        await page.get_by_label('Operations store').select_option('DT')
        await page.goto(BASE + '/summary')
        await page.get_by_label('Business date').fill('2026-09-15')
        await expect(page.get_by_label('Summary text')).to_be_visible()
        state['get_status_for_path'] = {'/api/store-events': (403, {'error': 'Assignment revoked'})}
        await page.clock.fast_forward(24 * 60 * 60 * 1000)
        await expect(page.get_by_label('Summary text')).to_have_count(0)
        await expect(page.get_by_role('alert')).to_contain_text('Assignment revoked')
        assert not errors, errors
    finally:
        await context.close()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--config', str(HERE/'vite.config.mjs')], cwd=FRONTEND, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        await wait_for_server(process)
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            for width in [390, 1280]:
                await flow(browser, width)
            context, page = await context_with_api(browser, {'mode':'data'}, auth={'role':'viewer'})
            await page.goto(BASE + '/')
            await expect(page.get_by_role('navigation', name='Store actions')).to_have_count(0)
            await page.goto(BASE + '/claw')
            await expect(page.get_by_role('button', name='Save record', exact=True)).to_have_count(0)
            await context.close()
            await actual_event_api(browser)
            await browser.close()
        print('PASS: staff home, checkout navigation, punch-in, event retries, summary privacy and mobile layout.')
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
