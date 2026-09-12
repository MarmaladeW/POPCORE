from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright, expect

from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server
from fixtures import payload as foundation_payload


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / '.local' / 'build-3' / 'browser'

PRODUCT = {
    'id': 1, 'product_id': 1, 'sku': '00123', 'jizhanming': 'Long bilingual product 商品名称',
    'name_cn_en': 'Long bilingual product 商品名称', 'stock_unit': 'piece',
    'identity_status': 'verified', 'upstairs_qty': 10, 'instore_qty': 2,
}
LOCATIONS = [
    {'id': 10, 'store_id': 1, 'store_code': 'DT', 'code': 'floor',
     'name': 'Downtown Floor', 'opening_verified': True},
    {'id': 11, 'store_id': 1, 'store_code': 'DT', 'code': 'upstairs',
     'name': 'Downtown Upstairs', 'opening_verified': True},
    {'id': 20, 'store_id': 2, 'store_code': 'MK', 'code': 'warehouse',
     'name': 'Markham Warehouse', 'opening_verified': True},
]


def api_payload(path, query, request):
    if path == '/api/stock':
        return {'items': [PRODUCT], 'total': 1}
    if path == '/api/inventory/locations':
        return LOCATIONS
    if path == '/api/goods/barcodes/resolve':
        if 'OUTSIDE' in query:
            return {'status': 'exact', 'candidates': [{
                'product_id': 999, 'stock_unit': 'set', 'quantity_per_scan': 1,
            }]}
        if 'AMB' in query:
            return {'status': 'ambiguous', 'candidates': [
                {'product_id': 1, 'stock_unit': 'piece', 'quantity_per_scan': 1},
                {'product_id': 2, 'stock_unit': 'piece', 'quantity_per_scan': 1},
            ]}
        if '00123' in query:
            return {'status': 'exact', 'candidates': [{
                'product_id': 1, 'stock_unit': 'piece', 'quantity_per_scan': 1,
            }]}
        return {'status': 'unknown', 'candidates': []}
    if path == '/api/goods/receipts/31' and request.method == 'GET':
        return {'id': 31, 'store_id': 1, 'destination_location_id': 10,
                'business_date': '2026-09-11', 'shipment_reference': 'SHIP-31',
                'supplier': 'Supplier', 'status': 'draft', 'version': 4,
                'lines': [{'line_no': 1, 'product_id': 1, 'native_unit': 'piece',
                           'expected_quantity': 5, 'saleable_quantity': 3,
                           'damaged_quantity': 1, 'hold_quantity': 1,
                           'discrepancy_note': 'One damaged'}]}
    if path == '/api/goods/receipts/31' and request.method == 'PATCH':
        return {'id': 31, 'version': 5, 'status': 'draft'}
    if path == '/api/goods/receipts':
        return {'id': 31, 'version': 1, 'status': 'draft'}
    if path == '/api/goods/receipts/31/post':
        return {'id': 31, 'version': 2, 'status': 'posted', 'inventory_document_id': 41}
    if path == '/api/goods/receipts/31/cancel':
        return {'id': 31, 'version': 5, 'status': 'cancelled'}
    if path == '/api/goods/transfers/51' and request.method == 'GET':
        return {'id': 51, 'kind': 'transfer', 'source_location_id': 11,
                'destination_location_id': 20, 'business_date': '2026-09-11',
                'status': 'active', 'version': 7, 'restock_session_id': None,
                'lines': [{'line_no': 1, 'product_id': 1, 'native_unit': 'piece',
                           'requested_quantity': 5, 'dispatched_quantity': 5,
                           'received_quantity': 2, 'returned_quantity': 0,
                           'loss_quantity': 0, 'short_quantity': 0,
                           'outstanding_transit': 3}]}
    if path == '/api/goods/transfers':
        return {'id': 51, 'version': 1, 'status': 'planned'}
    if path == '/api/goods/transfers/51/dispatch':
        return {'id': 51, 'version': 2, 'status': 'active', 'inventory_document_id': 61}
    if path == '/api/goods/transfers/51/receive':
        return {'id': 51, 'version': 3, 'status': 'completed', 'inventory_document_id': 62}
    if path == '/api/goods/transfers/51/return':
        return {'id': 51, 'version': 8, 'status': 'completed', 'inventory_document_id': 63}
    if path == '/api/goods/transfers/51/resolve_loss':
        return {'id': 51, 'version': 8, 'status': 'completed', 'inventory_document_id': 64}
    if path == '/api/goods/transfers/52' and request.method == 'GET':
        return {'id': 52, 'kind': 'transfer', 'source_location_id': 11,
                'destination_location_id': 20, 'business_date': '2026-09-11',
                'status': 'active', 'version': 9, 'restock_session_id': None,
                'lines': [{'line_no': 1, 'product_id': 1, 'native_unit': 'piece',
                           'requested_quantity': 5, 'dispatched_quantity': 3,
                           'received_quantity': 3, 'returned_quantity': 0,
                           'loss_quantity': 0, 'short_quantity': 0,
                           'outstanding_transit': 0}]}
    if path == '/api/goods/transfers/52/short_close':
        return {'id': 52, 'version': 10, 'status': 'completed'}
    if path == '/api/goods/counts/71' and request.method == 'GET':
        return {'id': 71, 'store_id': 1, 'location_id': 10,
                'disposition': 'saleable', 'business_date': '2026-09-11',
                'status': 'submitted', 'version': 2,
                'lines': [{'line_no': 1, 'product_id': 1, 'native_unit': 'piece',
                           'expected_quantity': 4, 'observed_quantity': 3,
                           'captured_balance_version': 8}]}
    if path == '/api/goods/counts':
        return {'id': 71, 'version': 1, 'status': 'draft'}
    if path == '/api/goods/counts/71/submit':
        return {'id': 71, 'version': 2, 'status': 'submitted'}
    if path == '/api/goods/counts/71/approve':
        return {'id': 71, 'version': 3, 'status': 'approved', 'inventory_document_id': 81}
    if path == '/api/goods/counts/71/return':
        return {'id': 71, 'version': 3, 'status': 'returned', 'recount_id': 72}
    if path == '/api/restock/session/91':
        return {'id': 91, 'store_id': 1, 'date': '2026-09-11', 'status': 'completed',
                'created_at': '2026-09-11T10:00:00', 'submitted_at': '2026-09-11T10:05:00',
                'completed_at': '2026-09-11T10:15:00', 'items': []}
    if path == '/api/goods/restock-suggestions':
        return {'location_id': 10, 'items': [{'product_id': 1, 'sku': '00123',
                'name': 'Long bilingual product 商品名称', 'unit': 'piece',
                'floor_quantity': 2, 'available_back_quantity': 3,
                'outstanding_inbound': 0, 'min_quantity': 4,
                'max_quantity': 8, 'suggested_quantity': 3}]}
    return foundation_payload(path, query, False)


async def select_option(page, index, text):
    await page.locator('.ant-select-selector').nth(index).click()
    await page.get_by_text(text, exact=True).last.click()


async def checks(browser, viewport):
    state = {'mode': 'data', 'api_payload': api_payload}
    context, page = await context_with_api(browser, state, viewport=viewport)
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/goods/receiving')
    await expect(page.get_by_text('Goods handling', exact=True)).to_be_visible()
    await select_option(page, 0, 'Downtown Floor')
    scan = page.get_by_label('Scan barcode')
    await scan.fill('OUTSIDE')
    await scan.press('Enter')
    await expect(page.get_by_text('+1 set', exact=False)).to_be_visible()
    await scan.fill('UNKNOWN')
    await scan.press('Enter')
    await expect(page.get_by_text('Unknown barcode. The receipt is unchanged.', exact=True)).to_be_visible()
    await scan.fill('AMB')
    await scan.press('Enter')
    await expect(page.get_by_text('Barcode is ambiguous. Select the exact product.', exact=True)).to_be_visible()
    await scan.fill('00123')
    await scan.press('Enter')
    await expect(page.get_by_text('+1 piece', exact=False)).to_be_visible()
    await scan.fill('00123')
    await scan.press('Enter')
    await expect(page.get_by_text('+2 piece', exact=False)).to_be_visible()
    await page.get_by_role('button', name='Save draft for review').click()
    await expect(page.get_by_text('Receipt #31', exact=True)).to_be_visible()
    await page.get_by_role('button', name='Post receipt').click()

    await page.goto(BASE + '/goods/transfers')
    await expect(page.get_by_text('Transfer stock', exact=True)).to_be_visible()
    await page.goto(BASE + '/goods/counts')
    await expect(page.get_by_text('Physical count', exact=True)).to_be_visible()
    await context.close()


async def resume_checks(browser):
    document_state = {'receipt_version': 4}

    def resume_payload(path, query, request):
        result = api_payload(path, query, request)
        if path == '/api/goods/receipts/31' and request.method == 'GET':
            result['version'] = document_state['receipt_version']
        elif path == '/api/goods/receipts/31' and request.method == 'PATCH':
            document_state['receipt_version'] += 1
            result['version'] = document_state['receipt_version']
        return result

    state = {'mode': 'data', 'api_payload': resume_payload}
    context, page = await context_with_api(browser, state, viewport={'width': 1280, 'height': 900}, auth={'role': 'manager'})
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")

    await page.goto(BASE + '/goods/receiving?receipt_id=31')
    await expect(page.get_by_text('Receipt #31', exact=True)).to_be_visible()
    await expect(page.get_by_text('SHIP-31', exact=True)).to_be_visible()
    await expect(page.get_by_text('3 pieces saleable', exact=True)).to_be_visible()
    assert not [request for request in state['requests'] if request['method'] != 'GET']
    await page.get_by_label('Line 1 saleable').fill('4')
    await expect(page.get_by_text('4 pieces saleable', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Post receipt', exact=True)).to_be_disabled()
    state['status_for_path'] = {'/api/goods/receipts/31': (503, {'error': 'Synthetic receipt lost response'})}
    await page.get_by_role('button', name='Save draft changes', exact=True).click()
    await expect(page.get_by_text('Synthetic receipt lost response', exact=True)).to_be_visible()
    await expect(page.get_by_label('Line 1 saleable')).to_be_disabled()
    await expect(page.get_by_role('button', name='Cancel draft', exact=True)).to_be_disabled()
    await page.get_by_role('button', name='Retry identical request', exact=True).click()
    patches = [request for request in state['requests'] if request['method'] == 'PATCH']
    assert len(patches) == 2 and patches[0]['idempotency_key'] == patches[1]['idempotency_key']
    assert patches[0]['post_data'] == patches[1]['post_data']
    state['status_for_path'] = {}
    await page.get_by_role('button', name='Retry identical request', exact=True).click()
    await expect(page.get_by_text('3 pieces saleable', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Post receipt', exact=True)).to_be_enabled()
    patch = patches[0]
    assert patch['post_data']['expected_version'] == 4
    assert patch['post_data']['lines'][0]['unit'] == 'piece'
    assert patch['post_data']['lines'][0]['saleable_quantity'] == 4
    await page.get_by_role('button', name='Cancel draft', exact=True).click()
    cancel = next(request for request in state['requests'] if request['path'].endswith('/cancel'))
    assert cancel['post_data'] == {'expected_version': 5}

    await page.evaluate("localStorage.setItem('popcore_selected_store', JSON.stringify({id:0,code:'ALL',name:'All Stores',color:'#6366f1'}))")
    await page.goto(BASE + '/goods/transfers?transfer_id=51')
    await expect(page.get_by_text('Transfer #51', exact=True)).to_be_visible()
    await expect(page.get_by_text('Downtown Upstairs → Markham Warehouse', exact=False)).to_be_visible()
    await expect(page.get_by_text('Outstanding: 3 pieces', exact=True)).to_be_visible()
    assert not [request for request in state['requests'] if request['path'].startswith('/api/goods/transfers/51/')]
    await page.get_by_label('Action quantity').fill('4')
    await page.get_by_role('button', name='Receive', exact=True).click()
    await expect(page.get_by_text('Quantity must be between 1 and 3. No transfer action was sent.', exact=True)).to_be_visible()
    assert not [request for request in state['requests'] if request['path'].endswith('/receive')]
    await page.get_by_label('Action quantity').fill('3')
    await page.get_by_role('button', name='Receive', exact=True).dblclick()
    receive = next(request for request in state['requests'] if request['path'].endswith('/receive'))
    assert receive['post_data']['expected_version'] == 7
    assert receive['post_data']['lines'] == [{'line_no': 1, 'quantity': 3, 'disposition': 'saleable'}]
    assert receive['idempotency_key']
    assert len([request for request in state['requests'] if request['path'].endswith('/receive')]) == 1
    state['status_for_path'] = {'/api/goods/transfers/51/return': (503, {'error': 'Synthetic lost response'})}
    await page.get_by_role('button', name='Return to source', exact=True).click()
    await expect(page.get_by_text('Synthetic lost response', exact=True)).to_be_visible()
    await expect(page.get_by_label('Action quantity')).to_be_disabled()
    await expect(page.get_by_label('Transfer reason')).to_be_disabled()
    await expect(page.get_by_role('button', name='Receive', exact=True)).to_be_disabled()
    await page.get_by_role('button', name='Retry identical request', exact=True).click()
    returns = [request for request in state['requests'] if request['path'].endswith('/return')]
    assert len(returns) == 2 and returns[0]['idempotency_key'] == returns[1]['idempotency_key']
    assert returns[0]['post_data'] == returns[1]['post_data']
    state['status_for_path'] = {}
    await page.get_by_role('button', name='Retry identical request', exact=True).click()
    await page.get_by_label('Transfer reason').fill('Transit loss reviewed')
    await page.get_by_role('button', name='Resolve loss', exact=True).click()
    loss = next(request for request in state['requests'] if request['path'].endswith('/resolve_loss'))
    assert loss['post_data']['reason'] == 'Transit loss reviewed'

    await page.goto(BASE + '/goods/transfers?transfer_id=52')
    await page.get_by_label('Action quantity').fill('2')
    await page.get_by_label('Transfer reason').fill('Supplier short shipment')
    await page.get_by_role('button', name='Short close', exact=True).click()
    short_close = next(request for request in state['requests'] if request['path'].endswith('/short_close'))
    assert short_close['post_data']['expected_version'] == 9
    assert short_close['post_data']['lines'][0]['quantity'] == 2

    await page.goto(BASE + '/goods/counts?count_id=71')
    await expect(page.get_by_text('Count #71', exact=True)).to_be_visible()
    await expect(page.get_by_text('Submitted for review', exact=True)).to_be_visible()
    assert not [request for request in state['requests'] if request['path'].startswith('/api/goods/counts/71/')]
    await page.get_by_label('Count review reason').fill('Shelf recounted')
    state['status_for_path'] = {'/api/goods/counts/71/approve': (409, {'error': 'Counted balance changed; recount required'})}
    await page.get_by_role('button', name='Approve adjustment', exact=True).click()
    await expect(page.get_by_text('Counted balance changed; recount required', exact=True)).to_be_visible()
    state['status_for_path'] = {}
    await page.get_by_label('Count review reason').fill('Shelf recounted after refresh')
    state['status_for_path'] = {'/api/goods/counts/71/return': (503, {'error': 'Synthetic count lost response'})}
    await page.get_by_role('button', name='Return for recount', exact=True).click()
    await expect(page.get_by_text('Synthetic count lost response', exact=True)).to_be_visible()
    await expect(page.get_by_label('Count review reason')).to_be_disabled()
    await expect(page.get_by_role('button', name='Approve adjustment', exact=True)).to_be_disabled()
    await page.get_by_role('button', name='Retry identical request', exact=True).click()
    count_returns = [request for request in state['requests'] if request['path'] == '/api/goods/counts/71/return']
    assert len(count_returns) == 2 and count_returns[0]['idempotency_key'] == count_returns[1]['idempotency_key']
    assert count_returns[0]['post_data'] == count_returns[1]['post_data']
    state['status_for_path'] = {}
    await page.get_by_role('button', name='Retry identical request', exact=True).click()
    await expect(page.get_by_text('Returned record preserved. Recount #72 is a new draft with copied observations.', exact=True)).to_be_visible()

    await page.evaluate("localStorage.setItem('popcore_selected_store', JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE + '/restock?session_id=91')
    await expect(page.get_by_text('补货单 #91', exact=False)).to_be_visible()
    await expect(page.get_by_role('cell', name='3 pieces', exact=True)).to_be_visible()
    await page.wait_for_timeout(1400)
    await expect(page.get_by_text('补货单 #91', exact=False)).to_be_visible()

    read_only = {'/api/restock/session/91'}
    assert all(request['method'] == 'GET' for request in state['requests'] if request['path'] in read_only)
    await context.close()


async def main():
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', 5174)) == 0:
            raise RuntimeError('Port 5174 is already in use')
    OUT.mkdir(parents=True, exist_ok=True)
    log = (OUT / 'vite.log').open('w', encoding='utf-8')
    command = ['node', str(FRONTEND / 'node_modules' / 'vite' / 'bin' / 'vite.js'),
               '--config', str(HERE / 'vite.config.mjs')]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    process = subprocess.Popen(command, cwd=FRONTEND, stdout=log,
                               stderr=subprocess.STDOUT, creationflags=flags)
    try:
        await wait_for_server(process)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                for viewport in ({'width': 390, 'height': 844},
                                 {'width': 768, 'height': 900},
                                 {'width': 1440, 'height': 1000}):
                    await checks(browser, viewport)
                await resume_checks(browser)
            except Exception:
                for context in browser.contexts:
                    if context.pages:
                        await context.pages[-1].screenshot(
                            path=OUT / 'failure.png', full_page=True)
                        break
                raise
            finally:
                await browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
        log.close()


if __name__ == '__main__':
    started = time.perf_counter()
    asyncio.run(main())
    print(f'Goods flow browser checks passed in {time.perf_counter() - started:.1f}s', flush=True)
