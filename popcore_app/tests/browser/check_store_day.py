from __future__ import annotations

import asyncio
import base64
import socket
import subprocess
import sys
import time
from pathlib import Path

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


def sale_detail(version=2):
    return {
        'sale_id': 41, 'version': version, 'status': 'posted',
        'financial_status': 'recorded', 'allocation_status': 'allocated',
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


def closing_detail():
    return {
        'closing_id': 71, 'store_id': 1, 'business_date': '2026-09-08',
        'status': 'draft', 'version': 1, 'intake_complete': 0,
        'source_token': 'fixture-token',
        'cash': {'opening_cash_cents': 65000, 'verified_cash_receipts_cents': 30000,
                 'expected_drawer_cents': 95000, 'unknown_cash_payment_ids': [],
                 'event_totals_cents': {'paid_in': 0, 'refund': 0, 'payout': 0, 'removal': 0}},
        'source_documents': {'sales': [{'id': 41, 'status': 'posted',
            'allocation_status': 'allocated', 'financial_status': 'recorded'}],
            'receipts': [], 'counts': []},
        'latest_cash_count': None, 'hard_blockers': ['sales_intake_incomplete'],
        'review_exceptions': [],
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
        return {'sale_id': 41, 'version': 1, 'status': 'draft'}
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
    await page.get_by_label('Actual unit price (cents)').fill('2000')
    await page.get_by_label('Actual tax (cents)').fill('520')
    await page.get_by_label('Actual collected total (cents)').fill('4520')
    await page.get_by_label('Cash').fill('4520')
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
    await expect(page.get_by_text('Sales completeness', exact=True)).to_be_visible()
    await expect(page.get_by_text('Source documents', exact=True)).to_be_visible()
    await expect(page.get_by_text('Cash events', exact=True)).to_be_visible()
    await expect(page.get_by_text('Cash count', exact=True)).to_be_visible()
    await expect(page.get_by_label('Opening coins (cents)')).to_have_value('')
    await expect(page.get_by_label('Retained coins (cents)')).to_have_value('')
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
    await expect(page.get_by_role('button', name='Record refund')).to_be_visible()
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
