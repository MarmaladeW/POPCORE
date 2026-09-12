from __future__ import annotations

import asyncio
import base64
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs

from playwright.async_api import async_playwright, expect

from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server
from fixtures import payload as foundation_payload


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / '.local' / 'build-4' / 'browser'
BUILD6_UI = ROOT / '.local' / 'build6' / 'ui'
PRODUCT = {
    'id': 1, 'product_id': 1, 'sku': 'SALE-1', 'jizhanming': 'Store Day Product',
    'name_cn_en': 'Store Day Product', 'stock_unit': 'piece',
    'identity_status': 'verified', 'upstairs_qty': 10, 'instore_qty': 10,
}


def sale_detail(version=2, status='posted'):
    return {
        'sale_id': 41, 'version': version, 'status': status,
        'business_date': '2026-09-08', 'entry_mode': 'already_paid',
        'financial_status': 'recorded' if status == 'posted' else 'draft',
        'allocation_status': 'allocated' if status == 'posted' else 'draft',
        'inventory_document_id': 51, 'unresolved_reasons': [],
        'collected_cents': 4520,
        'lines': [{'line_no': 1, 'quantity': 2, 'unit': 'piece',
                   'product_name_snapshot': 'Store Day Product'}],
        'payments': [{'id': 61, 'tender': 'cash', 'amount_cents': 4520,
                      'effective_amount_cents': 4520, 'state': 'recorded',
                      'events': [], 'evidence': [{'id': 81, 'status': 'pending',
                          'mime_type': 'image/png', 'byte_size': 68}]}],
        'sources': [{'source_system': 'clover', 'source_account': 'DT',
                     'source_reference': 'CLOVER-100'}], 'returns': [],
        'payment_total_cents': 4520, 'payment_difference_cents': 0,
    }


def closing_detail(status='draft', manager=False):
    count = None if status == 'draft' else {
        'id': 1, 'revision': 1, 'opening_coin_cents': 2500,
        'retained_coin_cents': 2000, 'counted_cents': 97000,
        'expected_cents': 95000, 'variance_cents': 2000,
        'retained_cents': 67000, 'removal_cents': 30000,
        'denomination_counts': {'10000': 0, '5000': 0, '2000': 48, '1000': 0,
                                '500': 0, '200': 0, '100': 1, '25': 0,
                                '10': 0, '5': 0},
    }
    return {
        'closing_id': 91 if status == 'closed' else 71, 'store_id': 1,
        'business_date': '2026-09-08', 'status': status, 'version': 3,
        'intake_complete': 1 if status != 'draft' else 0,
        'source_token': 'fixture-token',
        'cash': {'opening_cash_cents': 65000, 'verified_cash_receipts_cents': 30000,
                 'expected_drawer_cents': 95000, 'unknown_cash_payment_ids': [],
                 'event_totals_cents': {'paid_in': 0, 'refund': 0, 'payout': 0, 'removal': 0}},
        'source_documents': {'sales': [{'id': 41, 'status': 'posted',
            'allocation_status': 'allocated', 'financial_status': 'recorded'}],
            'receipts': [], 'counts': []},
        'latest_cash_count': count,
        'hard_blockers': ['sales_intake_incomplete'] if status == 'draft' else [],
        'review_exceptions': [],
        **({'tender_totals_cents': {'cash': 4520, 'card': 0, 'e_transfer': 0,
                                    'wechat': 0, 'alipay': 0},
            'unknown_payment_ids': [],
            'snapshot': {'snapshot_id': 7, 'counted_cents': 97000,
                         'expected_cents': 95000, 'variance_cents': 2000,
                         'retained_cents': 67000, 'removal_cents': 30000,
                         'accepted_exceptions': [], 'unknown_payment_ids': [77]},
            'late_adjustments': [{'id': 8, 'source_type': 'payment_event',
                                  'source_id': '61', 'reason': 'Late refund'}]}
           if status == 'closed' and manager else {}),
    }


def api_payload(path, query, request):
    if path == '/api/reports/inventory':
        return {'report': 'inventory', 'scope': 'DT', 'store_ids': [1],
                'filters': {}, 'generated_at': '2026-09-09T00:00:00Z',
                'items': [{'product_id': 1, 'sku': 'SALE-1', 'stock_unit': 'piece',
                           'location': 'Floor / 店面', 'disposition': 'saleable',
                           'quantity': 10, 'version': 2}], 'total_rows': 1}
    if path == '/api/stock':
        return {'items': [PRODUCT], 'total': 1}
    if path == '/api/sale-documents':
        return sale_detail(1, 'draft')
    if path == '/api/sale-documents/41/post':
        return {key: sale_detail()[key] for key in (
            'sale_id', 'version', 'status', 'financial_status', 'allocation_status',
            'inventory_document_id', 'unresolved_reasons')}
    if path == '/api/sale-documents/41/payments':
        return sale_detail(3)
    if path == '/api/sale-documents/41':
        return sale_detail(3)
    if path == '/api/payments/61/evidence':
        return {'evidence_id': 81, 'payment_id': 61, 'mime_type': 'image/png',
                'byte_size': 68, 'status': 'pending'}
    if path in ('/api/closing', '/api/closing/71'):
        return closing_detail()
    return foundation_payload(path, query, False)


async def staff_flow(browser, viewport):
    state = {'mode': 'data', 'api_payload': api_payload}
    context, page = await context_with_api(
        browser, state, viewport=viewport, auth={'role': 'staff'},
    )
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/sales/entry')
    await expect(page.get_by_text('Enter completed sale', exact=True)).to_be_visible()
    await page.get_by_label('Receipt or order reference').fill('CLOVER-100')
    await page.get_by_label('Product').click()
    await page.get_by_text('Store Day Product', exact=True).last.click()
    await page.get_by_label('Quantity').fill('2')
    await page.get_by_label('Actual unit price ($)').fill('20.00')
    await page.get_by_label('Actual tax ($)').fill('5.20')
    await page.get_by_label('Actual collected total ($)').fill('45.20')
    await page.get_by_label('Cash ($)').fill('20.00')
    await page.get_by_label('Card ($)').fill('25.20')
    await page.get_by_role('button', name='Save draft').click()
    await expect(page.get_by_text('Draft saved', exact=True)).to_be_visible()
    await page.get_by_role('button', name='Record sale').click()
    await expect(page.get_by_text('Sale recorded', exact=True)).to_be_visible()

    await page.goto(BASE + '/sales/payments/61/evidence')
    png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
    )
    await page.locator('input[type=file]').set_input_files({
        'name': 'receipt.png', 'mimeType': 'image/png', 'buffer': png,
    })
    await page.get_by_role('button', name='Upload privately').click()
    await expect(page.get_by_text('Evidence saved for review', exact=True)).to_be_visible()

    await page.goto(BASE + '/closing')
    await expect(page.get_by_text('Store closing', exact=True)).to_be_visible()
    await page.get_by_role('button', name='Start closing').click()
    await expect(page.get_by_text('Source completeness', exact=True)).to_be_visible()
    await expect(page.get_by_text('Cash activity', exact=True)).to_be_visible()
    await expect(page.get_by_text('Cash count', exact=True)).to_be_visible()
    await expect(page.get_by_label('Opening coins ($)')).to_have_value('')
    await expect(page.get_by_label('Retained coins ($)')).to_have_value('')
    width = await page.evaluate('document.body.scrollWidth')
    assert width <= viewport['width'] + 2, f'Store-day pages overflowed at {viewport["width"]}px'
    await context.close()


async def permission_and_zoom_checks(browser):
    context, page = await context_with_api(
        browser, {'mode': 'data', 'api_payload': api_payload}, auth={'role': 'viewer'},
    )
    await page.goto(BASE + '/sales/entry')
    await expect(page.get_by_text('Access Denied', exact=True)).to_be_visible()
    await page.goto(BASE + '/closing')
    await expect(page.get_by_text('Access Denied', exact=True)).to_be_visible()
    await context.close()


async def manager_review_checks(browser):
    def manager_payload(path, query, request):
        if path == '/api/closing/71':
            data = closing_detail()
            data.update({'status': 'submitted', 'version': 2,
                         'review_exceptions': ['payment_unverified:61', 'evidence_pending:81']})
            return data
        return api_payload(path, query, request)

    context, page = await context_with_api(
        browser, {'mode': 'data', 'api_payload': manager_payload},
        viewport={'width': 390, 'height': 844}, auth={'role': 'manager'},
    )
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/sales/documents/41')
    await expect(page.get_by_text('Source history', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Verify')).to_be_visible()
    await expect(page.get_by_role('button', name='Record monetary refund')).to_be_visible()
    await expect(page.get_by_role('button', name='Record physical return')).to_be_visible()
    await page.goto(BASE + '/closing?closing_id=71')
    await expect(page.get_by_role('button', name='Return for changes')).to_be_visible()
    await expect(page.get_by_label('payment_unverified:61 acceptance reason')).to_be_visible()
    await expect(page.get_by_label('evidence_pending:81 acceptance reason')).to_be_visible()
    await page.goto(BASE + '/reports')
    await expect(page.get_by_text('Operational reports', exact=True)).to_be_visible()
    assert await page.evaluate('document.body.scrollWidth') <= 392
    BUILD6_UI.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=BUILD6_UI / 'manager-reports-390.png', full_page=True)
    await context.close()


async def payment_retry_checks(browser):
    state = {'mode': 'data', 'api_payload': api_payload,
             'status_for_path': {'/api/sale-documents/41/payments':
                                 (503, {'error': 'payment save interrupted'})}}
    context, page = await context_with_api(
        browser, state, viewport={'width': 768, 'height': 900}, auth={'role': 'staff'},
    )
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/sales/entry')
    await page.evaluate("""history.pushState({...history.state, idx: history.state.idx + 1}, '', '/future'); history.back()""")
    await page.wait_for_url(BASE + '/sales/entry')
    await page.get_by_label('Receipt or order reference').fill('PAYMENT-RETRY')
    await page.get_by_label('Product').click()
    await page.get_by_text('Store Day Product', exact=True).last.click()
    await page.get_by_label('Quantity').fill('1')
    await page.get_by_label('Actual collected total ($)').fill('20.00')
    await page.get_by_label('Cash ($)').fill('20.00')
    await page.get_by_role('button', name='Save draft').click()
    await page.get_by_role('button', name='Record sale', exact=True).click()
    await expect(page.get_by_text(
        'Sale recorded; payment facts not confirmed.', exact=True)).to_be_visible()
    retry = page.get_by_role('button', name='Retry payment save', exact=True)
    await expect(retry).to_be_visible()
    await expect(page.get_by_role('button', name='View sale', exact=True)).to_be_disabled()
    dismissed_back = asyncio.Event()
    async def dismiss_back(dialog):
        await dialog.dismiss()
        dismissed_back.set()
    page.once('dialog', lambda dialog: asyncio.create_task(dismiss_back(dialog)))
    await page.evaluate('history.back()')
    await asyncio.wait_for(dismissed_back.wait(), timeout=2)
    await expect(retry).to_be_visible()
    assert page.url == BASE + '/sales/entry'
    dismissed_forward = asyncio.Event()
    async def dismiss_forward(dialog):
        await dialog.dismiss()
        dismissed_forward.set()
    page.once('dialog', lambda dialog: asyncio.create_task(dismiss_forward(dialog)))
    await page.evaluate('history.forward()')
    await asyncio.wait_for(dismissed_forward.wait(), timeout=2)
    await expect(retry).to_be_visible()
    assert page.url == BASE + '/sales/entry'
    state['status_for_path'].pop('/api/sale-documents/41/payments')
    await retry.click()
    await expect(page.get_by_text('Sale recorded', exact=True)).to_be_visible()
    posts = [request for request in state['requests']
             if request['path'] == '/api/sale-documents/41/post']
    payments = [request for request in state['requests']
                if request['path'] == '/api/sale-documents/41/payments']
    assert len(posts) == 1, posts
    assert len(payments) == 2, payments
    assert payments[0]['post_data'] == payments[1]['post_data']
    assert payments[0]['idempotency_key'] == payments[1]['idempotency_key']
    await context.close()


async def sale_safety_checks(browser):
    state = {'mode': 'data', 'api_payload': api_payload}
    context, page = await context_with_api(browser, state, auth={'role': 'staff'})
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/sales/entry')
    await page.get_by_label('Receipt or order reference').fill('ZERO-UNKNOWN')
    await page.get_by_label('Product').click()
    await page.get_by_text('Store Day Product', exact=True).last.click()
    await page.get_by_label('Actual tax ($)').fill('0.00')
    await page.get_by_label('Actual collected total ($)').fill('0.00')
    await page.get_by_label('Cash ($)').fill('0.00')
    await page.get_by_role('button', name='Save draft').click()
    body = next(request['post_data'] for request in state['requests']
                if request['path'] == '/api/sale-documents')
    assert body['subtotal_cents'] is None and body['source_tax_cents'] == 0
    assert body['gross_cents'] is None and body['collected_cents'] == 0
    await expect(page.get_by_text('Unknown', exact=True).first).to_be_visible()
    await expect(page.get_by_text('cash · $0.00', exact=True)).to_be_visible()
    await context.close()

    state = {'mode': 'data', 'api_payload': api_payload}
    context, page = await context_with_api(browser, state, auth={'role': 'staff'})
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/sales/entry')
    await page.get_by_label('Receipt or order reference').fill('OVERFLOW')
    await page.get_by_label('Product').click()
    await page.get_by_text('Store Day Product', exact=True).last.click()
    await page.get_by_label('Quantity').fill('2')
    await page.get_by_label('Actual unit price ($)').fill('90071992547409.91')
    await page.get_by_role('button', name='Save draft').click()
    await expect(page.get_by_text('Calculated sale total is too large.', exact=True)).to_be_visible()
    assert not [request for request in state['requests']
                if request['path'] == '/api/sale-documents' and request['method'] == 'POST']
    await context.close()

    state = {'mode': 'data', 'api_payload': api_payload,
             'status_for_path': {'/api/sale-documents':
                                 (503, {'error': 'draft response lost'})}}
    context, page = await context_with_api(browser, state, auth={'role': 'staff'})
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/sales/entry')
    await page.get_by_label('Receipt or order reference').fill('STORE-GUARD')
    await page.get_by_label('Product').click()
    await page.get_by_text('Store Day Product', exact=True).last.click()
    await page.get_by_role('button', name='Save draft').click()
    await expect(page.get_by_role('button', name='Retry draft save', exact=True)).to_be_visible()
    await page.locator('select').first.select_option('MK')
    await expect(page.get_by_text('Resolve or acknowledge the unconfirmed sale request before changing stores.', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Retry draft save', exact=True)).to_be_visible()
    assert await page.locator('select').first.input_value() == 'DT'
    await context.close()

    def scoped_products(path, query, request):
        if path == '/api/stock':
            item = dict(PRODUCT)
            item.update({'id': 2, 'product_id': 2, 'jizhanming': 'MK Product'})
            return {'items': [item], 'total': 1} if 'store_code=MK' in query else {'items': [PRODUCT], 'total': 1}
        return api_payload(path, query, request)

    state = {'mode': 'data', 'api_payload': scoped_products, 'delay_dt': True}
    context, page = await context_with_api(browser, state, auth={'role': 'staff'})
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/sales/entry')
    await page.locator('select').first.select_option('MK')
    await page.get_by_label('Product').click()
    await expect(page.get_by_text('MK Product', exact=True).last).to_be_visible()
    await page.wait_for_timeout(700)
    await expect(page.get_by_text('Store Day Product', exact=True)).to_have_count(0)
    await context.close()

    state = {'mode': 'data', 'api_payload': api_payload,
             'get_status_for_path': {'/api/payment-evidence/81/content':
                                     (403, {'error': 'Evidence access denied'})},
             'status_for_path': {'/api/payments/61/verify':
                                 (409, {'error': 'Sale changed before payment review'})}}
    context, page = await context_with_api(browser, state, auth={'role': 'manager'})
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/sales/documents/41')
    await expect(page.get_by_text('Private evidence preview is unavailable or access was denied.', exact=True)).to_be_visible()
    await expect(page.get_by_label('Payment 61 verification reason')).to_be_visible()
    await expect(page.get_by_label('Payment 61 rejection reason')).to_be_visible()
    await expect(page.get_by_label('Payment 61 refund reason')).to_be_visible()
    await expect(page.get_by_text('This records money only and does not return stock.', exact=True)).to_be_visible()
    await expect(page.get_by_text('It does not refund money.', exact=False)).to_be_visible()
    await expect(page.get_by_label('Return quantity (piece)')).to_be_visible()
    await page.get_by_label('Payment 61 verification reason').fill('Counted cash')
    await page.get_by_role('button', name='Verify').click()
    await expect(page.get_by_text('Saved facts changed. Review the refreshed sale before creating a new request.', exact=True)).to_be_visible()
    await expect(page.get_by_label('Payment 61 verification reason')).to_have_value('')
    await context.close()

    def verified_allocation_payload(path, query, request):
        if path == '/api/sale-documents/41':
            data = sale_detail(4)
            data.update({'allocation_status': 'pending',
                         'unresolved_reasons': ['product_mapping_required']})
            return data
        if path == '/api/products/search':
            verified = dict(PRODUCT)
            unverified = dict(PRODUCT)
            verified.update({'id': 2, 'jizhanming': 'Verified Choice'})
            unverified.update({'id': 3, 'jizhanming': 'Unverified Choice',
                               'identity_status': 'unverified'})
            return [verified, unverified]
        return api_payload(path, query, request)

    context, page = await context_with_api(
        browser, {'mode': 'data', 'api_payload': verified_allocation_payload},
        auth={'role': 'manager'},
    )
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/sales/documents/41')
    product = page.get_by_role('combobox', name='Line 1 verified product')
    await product.fill('Choice')
    await expect(page.get_by_text('SALE-1 Verified Choice', exact=True).last).to_be_visible()
    await expect(page.get_by_text('SALE-1 Unverified Choice', exact=True)).to_have_count(0)
    await context.close()

    def allocation_payload(path, query, request):
        if path == '/api/sale-documents/41':
            data = sale_detail(4)
            data.update({'allocation_status': 'pending',
                         'unresolved_reasons': ['fresh_set_selection_required']})
            return data
        return api_payload(path, query, request)

    context, page = await context_with_api(
        browser, {'mode': 'data', 'api_payload': allocation_payload}, auth={'role': 'manager'},
    )
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/sales/documents/41')
    await expect(page.get_by_text('Opened-set provenance is required', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Allocate stock')).to_have_count(0)
    await context.close()


async def closing_resume_checks(browser):
    def manager_payload(path, query, request):
        if path == '/api/closing/91':
            return closing_detail('closed', manager=True)
        return api_payload(path, query, request)

    context, page = await context_with_api(
        browser, {'mode': 'data', 'api_payload': manager_payload},
        auth={'role': 'manager'},
    )
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/closing?closing_id=91')
    await expect(page.get_by_text('Store day closed', exact=True)).to_be_visible()
    await expect(page.get_by_text('Immutable closing snapshot', exact=True)).to_be_visible()
    await expect(page.get_by_text('Saved unknown payment references', exact=True)).to_be_visible()
    await expect(page.get_by_text('Payment #77', exact=True)).to_be_visible()
    await expect(page.get_by_text('Late refund', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Save cash count', exact=True)).to_have_count(0)
    await expect(page.get_by_role('button', name='Close store day', exact=True)).to_have_count(0)
    await context.close()

    def mismatch_payload(path, query, request):
        if path == '/api/closing/71':
            return closing_detail('submitted', manager=True)
        return api_payload(path, query, request)

    context, page = await context_with_api(
        browser, {'mode': 'data', 'api_payload': mismatch_payload}, auth={'role': 'manager'},
    )
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:2,code:'MK',name:'Market',color:'#6366f1'}))""")
    await page.goto(BASE + '/closing?closing_id=71')
    await expect(page.get_by_text("Select the closing document's store to make changes.", exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Close store day')).to_have_count(0)
    await context.close()

    def intake_payload(path, query, request):
        if path == '/api/closing/71':
            data = closing_detail('draft', manager=True)
            data['hard_blockers'] = []
            return data
        return api_payload(path, query, request)
    state = {'mode': 'data', 'api_payload': intake_payload,
             'status_for_path': {'/api/closing/71':
                                 (503, {'error': 'intake response lost'})}}
    context, page = await context_with_api(browser, state, auth={'role': 'manager'})
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/closing?closing_id=71')
    intake = page.get_by_text('All completed POS sales and missing transactions for this day have been entered.')
    await intake.click()
    retry = page.get_by_role('button', name='Retry identical request')
    await expect(retry).to_be_visible()
    state['status_for_path'].pop('/api/closing/71')
    await retry.click()
    updates = [item for item in state['requests']
               if item['path'] == '/api/closing/71' and item['method'] == 'PATCH']
    assert len(updates) == 2 and updates[0]['post_data'] == updates[1]['post_data']
    assert updates[0]['post_data']['intake_complete'] is True
    assert updates[0]['idempotency_key'] == updates[1]['idempotency_key']
    await context.close()

    def staff_payload(path, query, request):
        if path == '/api/closing/91':
            return closing_detail('closed', manager=False)
        return api_payload(path, query, request)

    context, page = await context_with_api(
        browser, {'mode': 'data', 'api_payload': staff_payload}, auth={'role': 'staff'},
    )
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/closing?closing_id=91')
    await expect(page.get_by_text('Store day closed', exact=True)).to_be_visible()
    await expect(page.get_by_text('Immutable closing snapshot', exact=True)).to_have_count(0)
    await expect(page.get_by_role('button', name='Save cash count', exact=True)).to_have_count(0)
    await context.close()

    def conflict_payload(path, query, request):
        if path == '/api/closing/71':
            return closing_detail('submitted', manager=True)
        return api_payload(path, query, request)

    state = {'mode': 'data', 'api_payload': conflict_payload,
             'status_for_path': {'/api/closing/71/close':
                                 (409, {'error': 'Closing sources changed; refresh and review'})}}
    context, page = await context_with_api(browser, state, auth={'role': 'manager'})
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/closing?closing_id=71')
    await page.get_by_role('button', name='Close store day').click()
    await expect(page.get_by_text(
        'Closing sources changed. Review the refreshed facts before creating a new request.',
        exact=True)).to_be_visible()
    assert len([request for request in state['requests']
                if request['path'] == '/api/closing/71' and request['method'] == 'GET']) >= 2
    await context.close()

    returned = {'status': 'submitted'}
    def return_payload(path, query, request):
        if path == '/api/closing/71/return':
            returned['status'] = 'draft'
            return closing_detail('draft', manager=True)
        if path == '/api/closing/71':
            return closing_detail(returned['status'], manager=True)
        return api_payload(path, query, request)
    state = {'mode': 'data', 'api_payload': return_payload}
    context, page = await context_with_api(browser, state, auth={'role': 'manager'})
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/closing?closing_id=71')
    await page.get_by_label('Return-for-changes reason').fill('Reconcile the cash source')
    await page.get_by_role('button', name='Return for changes').click()
    await expect(page.get_by_text('draft', exact=True)).to_be_visible()
    request = next(item for item in state['requests'] if item['path'] == '/api/closing/71/return')
    assert request['post_data']['reason'] == 'Reconcile the cash source'
    assert request['idempotency_key']
    await context.close()

    def counted_draft(path, query, request):
        if path == '/api/closing/71':
            data = closing_detail('submitted', manager=True)
            data.update({'status': 'draft', 'hard_blockers': []})
            return data
        return api_payload(path, query, request)
    state = {'mode': 'data', 'api_payload': counted_draft,
             'status_for_path': {'/api/closing/71/cash-events':
                                 (503, {'error': 'cash event response lost'})}}
    context, page = await context_with_api(browser, state, auth={'role': 'manager'})
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/closing?closing_id=71')
    await expect(page.get_by_label('Opening coins ($)')).to_have_value('25.00')
    await expect(page.get_by_label('Retained coins ($)')).to_have_value('20.00')
    await expect(page.get_by_label('$20 count', exact=True)).to_have_value('48')
    await page.get_by_label('Cash event amount ($)').fill('1.25')
    await page.get_by_label('Cash event reason').fill('Till correction')
    await page.get_by_role('button', name='Record cash event').click()
    retry = page.get_by_role('button', name='Retry identical request')
    await expect(retry).to_be_visible()
    await page.locator('select').first.select_option('MK')
    await expect(page.get_by_text('Resolve or acknowledge the unconfirmed closing request before changing stores.', exact=True)).to_be_visible()
    await expect(retry).to_be_visible()
    assert await page.locator('select').first.input_value() == 'DT'
    state['status_for_path'].pop('/api/closing/71/cash-events')
    await retry.click()
    events = [item for item in state['requests'] if item['path'] == '/api/closing/71/cash-events']
    assert len(events) == 2 and events[0]['post_data'] == events[1]['post_data']
    assert events[0]['idempotency_key'] == events[1]['idempotency_key']
    assert events[0]['post_data']['amount_cents'] == 125
    await context.close()

    context, page = await context_with_api(
        browser, {'mode': 'data', 'api_payload': manager_payload}, auth={'role': 'manager'},
    )
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:0,code:'ALL',name:'All Stores',color:'#6366f1'}))""")
    await page.goto(BASE + '/closing?closing_id=91')
    await expect(page.get_by_text('Store day closed', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Close store day')).to_have_count(0)
    await context.close()


async def report_checks(browser):
    def reports_payload(path, query, request):
        if path == '/api/reports/sales':
            page = int(parse_qs(query).get('page', ['1'])[0])
            start = (page - 1) * 50 + 1
            items = [
                {'store_id': 1, 'series': 'Core', 'design': f'Design {index}',
                 'unverified_product': None, 'product_id': index,
                 'native_unit': 'piece', 'quantity': index, 'line_count': 1,
                 'identity_complete': 1}
                for index in range(start, min(start + 50, 126))
            ]
            return {'report': 'sales', 'scope': 'DT', 'store_ids': [1],
                    'filters': {}, 'generated_at': '2026-09-11T00:00:00Z',
                    'items': items, 'total_rows': 125,
                    'known_gross_cents': 12500, 'incomplete_count': 1,
                    'gross_complete': False}
        return api_payload(path, query, request)

    state = {
        'mode': 'data', 'api_payload': reports_payload,
        'get_status_for_path': {
            '/api/reports/sales/export.csv': (413, {'error': 'Export exceeds 500 rows'}),
        },
    }
    context, page = await context_with_api(
        browser, state, viewport={'width': 1280, 'height': 900},
        auth={'role': 'manager'},
    )
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/reports?report=sales&from=2026-09-01&to=2026-09-30&page=1')
    await expect(page.get_by_role('heading', name='Sales report', exact=True)).to_be_visible()
    await expect(page.get_by_text('Known gross: $125.00', exact=True)).to_be_visible()
    await expect(page.get_by_text('1 sale has an unknown gross total.', exact=True)).to_be_visible()
    await expect(page.get_by_role('cell', name='Design 1', exact=True)).to_be_visible()
    await expect(page.get_by_text('125 rows', exact=True)).to_be_visible()
    await page.get_by_title('2').click()
    await expect(page.get_by_role('cell', name='Design 51', exact=True)).to_be_visible()
    await page.get_by_role('button', name='Export CSV', exact=True).click()
    await expect(page.get_by_text('Export exceeds 500 rows. Narrow the store or date filters, then try again.', exact=True)).to_be_visible()
    report_queries = [request['query'] for request in state['requests']
                      if request['path'] == '/api/reports/sales']
    assert any('page=1' in query and 'page_size=50' in query for query in report_queries)
    assert any('page=2' in query and 'page_size=50' in query for query in report_queries)
    export_query = next(request['query'] for request in state['requests']
                        if request['path'] == '/api/reports/sales/export.csv')
    assert 'store_code=DT' in export_query and 'from=2026-09-01' in export_query and 'to=2026-09-30' in export_query
    assert 'page=' not in export_query and 'page_size=' not in export_query
    await context.close()

    context, page = await context_with_api(
        browser, {'mode': 'data', 'api_payload': api_payload},
        viewport={'width': 768, 'height': 900}, auth={'role': 'staff'},
    )
    await context.add_init_script("""localStorage.setItem('popcore_selected_store',
      JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))""")
    await page.goto(BASE + '/sales/entry')
    await page.evaluate("document.documentElement.style.zoom='2'")
    await expect(page.get_by_text('Enter completed sale', exact=True)).to_be_visible()
    await page.get_by_label('Receipt or order reference').focus()
    await page.keyboard.type('KEYBOARD-ENTRY')
    await expect(page.get_by_label('Receipt or order reference')).to_have_value('KEYBOARD-ENTRY')
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
                    await staff_flow(browser, viewport)
                await permission_and_zoom_checks(browser)
                await manager_review_checks(browser)
                await payment_retry_checks(browser)
                await sale_safety_checks(browser)
                await closing_resume_checks(browser)
                await report_checks(browser)
            except Exception:
                for context in browser.contexts:
                    if context.pages:
                        await context.pages[-1].screenshot(path=OUT / 'failure.png', full_page=True)
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
    print(f'Store-day browser checks passed in {time.perf_counter() - started:.1f}s', flush=True)
