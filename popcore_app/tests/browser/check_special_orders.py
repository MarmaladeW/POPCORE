"""Isolated Special Orders workflow checks; no live authentication."""
import asyncio
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright, expect

from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server
from fixtures import payload


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
                status='open', created_by='fixture|staff', created_by_name='Alex Chen',
                created_at='2026-09-21T14:00:00Z', completed_by=None,
                completed_by_name=None, completed_at=None, version=1,
                payments=[], paid_cents=0, remaining_cents=data['total_cents'],
            )
            if data['initial_paid_cents']:
                order['payments'].append(dict(
                    id=1, amount_cents=data['initial_paid_cents'],
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
                id=2, amount_cents=data['amount_cents'],
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
        if path == '/api/special-orders/1':
            return orders[0]
        return payload(path, query)

    state = {'mode': 'data', 'api_payload': api}
    context, page = await context_with_api(
        browser, state, viewport={'width': 1280, 'height': 900},
        auth={'role': 'staff'},
    )
    await page.goto(BASE + '/special-orders')
    await expect(page.get_by_role('heading', name='Special Orders', exact=True)).to_be_visible()

    await page.get_by_role('button', name='New special order', exact=True).click()
    await page.get_by_label('Customer name').fill('Maya Chen')
    await page.get_by_label('Phone number').fill('416-555-0148')
    await page.get_by_label('Item description').fill('Smiski Museum Series - The Source')
    await page.get_by_label('Total price').fill('100.00')
    await page.get_by_label('Amount paid now').fill('20.00')
    await page.get_by_role('button', name='Create order', exact=True).click()

    await expect(page.get_by_role('heading', name='Maya Chen', exact=True)).to_be_visible()
    await expect(page.get_by_text('Phone hidden unless you are today\'s Cashier or a manager.')).to_be_visible()
    await expect(page.get_by_text('$80.00 remaining', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Mark customer received', exact=True)).to_be_disabled()

    await page.get_by_label('Payment amount').fill('80.00')
    await page.get_by_role('button', name='Add payment', exact=True).click()
    await expect(page.locator('strong').filter(has_text='Paid in full')).to_be_visible()
    complete = page.get_by_role('button', name='Mark customer received', exact=True)
    await expect(complete).to_be_enabled()
    await complete.click()
    await expect(page.get_by_text('Customer received the item', exact=True)).to_be_visible()

    await page.get_by_role('tab', name='Completed', exact=True).click()
    await expect(page.get_by_role('button', name='Maya Chen', exact=False)).to_be_visible()
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')

    await page.set_viewport_size({'width': 390, 'height': 844})
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await page.get_by_role('button', name='More', exact=True).click()
    await expect(page.get_by_role('link', name='Special orders', exact=True)).to_be_visible()

    posts = [request for request in state['requests'] if request['method'] == 'POST']
    assert [request['path'] for request in posts] == [
        '/api/special-orders',
        '/api/special-orders/1/payments',
        '/api/special-orders/1/complete',
    ]
    assert all(request['idempotency_key'] for request in posts)
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
            await browser.close()
        print('PASS: Special Orders create, separate payment, full-payment handoff, history, phone masking, mobile navigation, and responsive width.')
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
