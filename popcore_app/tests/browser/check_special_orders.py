"""Isolated Special Orders workflow checks; no live authentication."""
import asyncio
import re
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright, expect

from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server
from fixtures import STORES, payload


async def checks(browser):
    orders = []

    def api(path, query, request):
        if path == '/api/special-orders' and request.method == 'GET':
            status = 'completed' if 'status=completed' in query else 'open'
            return [order for order in orders if order['status'] == status]
        if path == '/api/special-orders' and request.method == 'POST':
            data = request.post_data_json
            order = dict(
                id=1, customer_name=data['customer_name'], customer_phone=None,
                item_description=data['item_description'], total_cents=data['total_cents'],
                pickup_store_id=data['pickup_store_id'], pickup_store_code='DT',
                pickup_store_name='Downtown',
                status='open', created_by='fixture|staff', created_by_name='Alex Chen',
                created_at='2026-09-21T14:00:00Z', completed_by=None,
                completed_by_name=None, completed_at=None, version=1,
                payments=[], paid_cents=0, remaining_cents=data['total_cents'],
            )
            if data['initial_paid_cents']:
                order['payments'].append(dict(
                    id=1, amount_cents=data['initial_paid_cents'],
                    tender=data['initial_tender'],
                    paid_at='2026-09-21T14:00:00Z',
                ))
                order['paid_cents'] = data['initial_paid_cents']
                order['remaining_cents'] -= data['initial_paid_cents']
            orders.append(order)
            return order
        if path == '/api/special-orders/1/payments':
            data = request.post_data_json
            order = orders[0]
            order['payments'].append(dict(
                id=2, amount_cents=data['amount_cents'], tender=data['tender'],
                paid_at='2026-09-21T15:00:00Z',
            ))
            order['paid_cents'] += data['amount_cents']
            order['remaining_cents'] -= data['amount_cents']
            order['version'] += 1
            return order
        if path == '/api/special-orders/1/complete':
            order = orders[0]
            order.update(
                status='completed', completed_by='fixture|staff',
                completed_by_name='Alex Chen', completed_at='2026-09-21T15:05:00Z',
                version=order['version'] + 1,
            )
            return order
        if path == '/api/special-orders/1' and request.method == 'PATCH':
            order = orders[0]
            store = next(store for store in STORES if store['id'] == request.post_data_json['pickup_store_id'])
            order.update(
                pickup_store_id=store['id'], pickup_store_code=store['code'],
                pickup_store_name=store['name'], version=order['version'] + 1,
            )
            return order
        if path == '/api/special-orders/1':
            return orders[0]
        return payload(path, query)

    state = {'mode': 'data', 'api_payload': api}
    context, page = await context_with_api(
        browser, state, viewport={'width': 1280, 'height': 900},
        auth={'role': 'staff'},
    )
    await page.goto(BASE + '/special-orders')
    await expect(page.get_by_role('link', name='特殊订单 Special orders', exact=False)).to_have_attribute('aria-current', 'page')

    await page.get_by_role('button', name=re.compile('New special order')).click()
    await page.get_by_label('Customer name').fill('Maya Chen')
    await page.get_by_label('Phone number').fill('416-555-0148')
    await page.get_by_label('Item description').fill('Smiski Museum Series - The Source')
    await page.get_by_label('Pickup location').select_option('1')
    await page.get_by_label('Total price').fill('100.00')
    await page.get_by_label('Amount paid now').fill('20.00')
    await page.get_by_label('Initial payment method').select_option('cash')
    await page.get_by_role('button', name='Create order', exact=True).click()

    await expect(page.get_by_role('heading', name='Maya Chen', exact=True)).to_be_visible()
    await expect(page.get_by_text('Phone hidden unless you are today\'s Cashier or a manager.')).to_be_visible()
    await expect(page.locator('dd').get_by_text('Downtown · DT', exact=True)).to_be_visible()
    await expect(page.get_by_text('$80.00 remaining', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Mark customer received', exact=True)).to_be_disabled()

    await page.get_by_label('Pickup location').select_option('2')
    await page.get_by_role('button', name='Update pickup location', exact=True).click()
    await expect(page.locator('dd').get_by_text('Markham · MK', exact=True)).to_be_visible()

    await page.get_by_label('Payment amount').fill('80.00')
    await page.get_by_label('Payment method').select_option('wechat')
    await page.get_by_role('button', name='Add payment', exact=True).click()
    await expect(page.locator('.so-payments b').get_by_text('Cash', exact=True)).to_be_visible()
    await expect(page.locator('.so-payments b').get_by_text('WeChat Pay', exact=True)).to_be_visible()
    await expect(page.locator('strong').filter(has_text='Paid in full')).to_be_visible()
    complete = page.get_by_role('button', name=re.compile('Mark customer received'))
    await expect(complete).to_be_enabled()
    await complete.click()
    await expect(page.get_by_text('Customer received the item', exact=True)).to_be_visible()

    await page.get_by_role('tab', name='已取货 Received', exact=False).click()
    await expect(page.get_by_role('button', name='Maya Chen', exact=False)).to_be_visible()
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')

    await page.set_viewport_size({'width': 390, 'height': 844})
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    # Until special orders move inside 买单 (Checkout section), 更多 keeps a 特殊订单 row.
    # Special orders live inside 买单 now: a row on the phone checkout page.
    await page.get_by_role('link', name='买单 Checkout', exact=True).click()
    await expect(page.get_by_role('navigation', name='Checkout tools').get_by_role('link', name='特殊订单 Special orders', exact=False)).to_be_visible()

    posts = [request for request in state['requests'] if request['method'] == 'POST']
    assert [request['path'] for request in posts] == [
        '/api/special-orders',
        '/api/special-orders/1/payments',
        '/api/special-orders/1/complete',
    ]
    assert all(request['idempotency_key'] for request in posts)
    assert posts[0]['post_data']['pickup_store_id'] == 1
    assert posts[0]['post_data']['initial_tender'] == 'cash'
    assert posts[1]['post_data']['tender'] == 'wechat'
    patches = [request for request in state['requests'] if request['method'] == 'PATCH']
    assert patches[0]['post_data']['pickup_store_id'] == 2
    await context.close()


async def admin_corrections(browser):
    """Switching between two completed orders at the same version must not carry one order's
    values into the other's correction form, and a save sends only what the admin changed."""
    def completed(order_id, name, creator, creator_name, store):
        return dict(
            id=order_id, customer_name=name, customer_phone=None, item_description='Figure',
            total_cents=5000, pickup_store_id=store['id'], pickup_store_code=store['code'],
            pickup_store_name=store['name'], status='completed', created_by=creator,
            created_by_name=creator_name, created_at='2026-10-20T15:42:00Z', completed_by=creator,
            completed_by_name=creator_name, completed_at='2026-10-24T18:00:00Z', version=2,
            payments=[], paid_cents=5000, remaining_cents=0,
        )
    orders = [completed(1, 'Order A', 'fixture|alice', 'Alice', STORES[0]),
              completed(2, 'Order B', 'fixture|carol', 'Carol', STORES[1])]

    def api(path, query, request):
        if path == '/api/special-orders' and request.method == 'GET':
            return [order for order in orders if ('status=completed' in query) == (order['status'] == 'completed')]
        if path == '/api/schedule/employees':
            return [dict(auth0_id='fixture|alice', name='Alice'), dict(auth0_id='fixture|carol', name='Carol')]
        if path == '/api/special-orders/2' and request.method == 'PATCH':
            orders[1].update(request.post_data_json, version=orders[1]['version'] + 1)
            orders[1].pop('expected_version', None)
            return orders[1]
        return payload(path, query)

    state = {'mode': 'data', 'api_payload': api}
    context, page = await context_with_api(
        browser, state, viewport={'width': 1280, 'height': 900}, auth={'role': 'admin'},
    )
    await page.goto(BASE + '/special-orders')
    await page.get_by_role('tab', name='已取货 Received', exact=False).click()
    await page.get_by_role('button', name='Order A', exact=False).click()
    await expect(page.get_by_label('Created by')).to_have_value('fixture|alice')
    await page.get_by_role('button', name='Order B', exact=False).click()
    await expect(page.get_by_role('heading', name='Order B', exact=True)).to_be_visible()
    await page.get_by_text('Admin corrections', exact=True).click()
    await expect(page.get_by_label('Created by')).to_have_value('fixture|carol')
    await expect(page.get_by_label('Completed by')).to_have_value('fixture|carol')
    await expect(page.locator('.so-admin').get_by_label('Pickup location')).to_have_value(str(STORES[1]['id']))
    await page.get_by_label('Completion date').fill('2026-10-24T15:30')
    await page.get_by_role('button', name='Save corrections', exact=True).click()
    await expect(page.get_by_text('Save corrections', exact=True)).to_be_visible()
    patches = [request for request in state['requests'] if request['method'] == 'PATCH']
    assert [request['path'] for request in patches] == ['/api/special-orders/2'], patches
    assert set(patches[0]['post_data']) == {'expected_version', 'completed_at'}, patches[0]['post_data']
    assert orders[1]['created_by'] == 'fixture|carol' and orders[1]['pickup_store_id'] == STORES[1]['id']
    await context.close()


async def unconfirmed_request(browser):
    """A payment whose response was lost stays the only pending change: other orders' actions wait until it is
    retried with its own key (or discarded), and it is never re-sent in place of another order's payment."""
    def open_order(order_id, name, total):
        return dict(
            id=order_id, customer_name=name, customer_phone=None, item_description='Figure', total_cents=total,
            pickup_store_id=1, pickup_store_code='DT', pickup_store_name='Downtown', status='open',
            created_by='fixture|staff', created_by_name='Alex Chen', created_at='2026-10-20T15:42:00Z',
            completed_by=None, completed_by_name=None, completed_at=None, version=1,
            payments=[], paid_cents=0, remaining_cents=total,
        )
    orders = [open_order(41, 'Alice', 10000), open_order(42, 'Bob', 8000)]

    def api(path, query, request):
        if path == '/api/special-orders' and request.method == 'GET':
            return [order for order in orders if order['status'] == 'open']
        match = re.fullmatch(r'/api/special-orders/(\d+)/payments', path)
        if match:
            order = next(order for order in orders if order['id'] == int(match.group(1)))
            data = request.post_data_json
            order['payments'].append(dict(id=len(order['payments']) + 1, amount_cents=data['amount_cents'],
                                          tender=data['tender'], paid_at='2026-10-21T15:00:00Z'))
            order['paid_cents'] += data['amount_cents']
            order['remaining_cents'] -= data['amount_cents']
            order['version'] += 1
            return order
        return payload(path, query)

    state = {'mode': 'data', 'api_payload': api,
             'status_for_path': {'/api/special-orders/41/payments': (503, {'error': 'Gateway timeout'})}}
    context, page = await context_with_api(
        browser, state, viewport={'width': 1280, 'height': 900}, auth={'role': 'staff'},
    )
    await page.goto(BASE + '/special-orders')
    await page.get_by_role('button', name='Alice', exact=False).click()
    await page.get_by_label('Payment amount').fill('50.00')
    await page.get_by_label('Payment method').select_option('cash')
    await page.get_by_role('button', name='Add payment', exact=True).click()
    await expect(page.get_by_text('Unconfirmed: payment for order #41', exact=False)).to_be_visible()

    await page.get_by_role('button', name='Bob', exact=False).click()
    await expect(page.get_by_role('heading', name='Bob', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Add payment', exact=True)).to_be_disabled()
    await expect(page.get_by_role('button', name='Update pickup location', exact=True)).to_be_disabled()
    await page.get_by_label('Payment amount').fill('30.00')
    await page.get_by_label('Payment method').select_option('e_transfer')
    await page.get_by_label('Payment amount').press('Enter')
    await page.wait_for_timeout(300)
    await expect(page.get_by_role('heading', name='Bob', exact=True)).to_be_visible()
    payments = lambda: [request for request in state['requests'] if request['method'] == 'POST']
    assert [request['path'] for request in payments()] == ['/api/special-orders/41/payments'], payments()

    state['status_for_path'] = {}
    await page.get_by_role('button', name='Retry', exact=True).click()
    await expect(page.get_by_role('heading', name='Alice', exact=True)).to_be_visible()
    await expect(page.get_by_text('Unconfirmed:', exact=False)).to_have_count(0)
    first, retried = payments()
    assert retried['path'] == first['path'] and retried['idempotency_key'] == first['idempotency_key'], payments()
    assert retried['post_data'] == first['post_data'] and first['post_data']['amount_cents'] == 5000

    await page.get_by_role('button', name='Bob', exact=False).click()
    await page.get_by_label('Payment amount').fill('30.00')
    await page.get_by_label('Payment method').select_option('e_transfer')
    await page.get_by_role('button', name='Add payment', exact=True).click()
    await expect(page.get_by_text('$50.00 remaining', exact=True)).to_be_visible()
    bob = payments()[-1]
    assert bob['path'] == '/api/special-orders/42/payments' and bob['idempotency_key'] != first['idempotency_key'], bob
    assert bob['post_data']['amount_cents'] == 3000 and bob['post_data']['tender'] == 'e_transfer', bob
    await context.close()


async def main():
    process = subprocess.Popen(
        ['node', 'node_modules/vite/bin/vite.js', '--config', str(HERE / 'vite.config.mjs')],
        cwd=FRONTEND, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        await wait_for_server(process)
        async with async_playwright() as playwright:
            chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
            browser = await playwright.chromium.launch(
                executable_path=str(chrome) if chrome.exists() else None,
            )
            await checks(browser)
            await admin_corrections(browser)
            await unconfirmed_request(browser)
            await browser.close()
        print('PASS: Special Orders create, separate payment, full-payment handoff, history, phone masking, mobile navigation, responsive width, per-order admin corrections, and unconfirmed-request retry.')
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
