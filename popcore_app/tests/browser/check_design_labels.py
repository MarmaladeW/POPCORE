"""Named designs remain explicit when receiving and allocating saved sales."""
import asyncio
import subprocess

from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT, context_with_api
from check_goods_flow import api_payload as goods_payload
from check_store_day import sale_detail

BASE = 'http://127.0.0.1:5190'
OUT = ROOT / '.local' / 'design-labels'
DESIGN = {'id': 999, 'sku': 'LEGACY-999', 'jizhanming': 'Generic legacy item',
          'series_name': 'Night Garden', 'design_name': 'Moon',
          'stock_form': 'confirmed_design', 'stock_unit': 'piece', 'identity_status': 'verified'}
LABEL = 'Night Garden · Moon · Confirmed'


def payload(path, query, request):
    if path == '/api/products/999':
        return DESIGN
    if path == '/api/products/search':
        return [DESIGN]
    if path == '/api/sale-documents/41':
        return {**sale_detail(), 'allocation_status': 'pending', 'payments': [],
                'unresolved_reasons': ['unmapped_product']}
    result = goods_payload(path, query, request)
    if path in ('/api/goods/receipts/31','/api/goods/counts/71','/api/goods/transfers/51') and request.method == 'GET':
        result['lines'][0]['product_id'] = 999
    return result


async def receipt_check(browser, width):
    state = {'mode': 'data', 'api_payload': payload}
    context, page = await context_with_api(browser, state, viewport={'width': width, 'height': 900})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown'}))")
    try:
        await page.goto(BASE + '/goods/receiving?receipt_id=31')
        await expect(page.get_by_text('Receipt #31', exact=True)).to_be_visible()
        await expect(page.get_by_text(LABEL, exact=True)).to_be_visible()
        assert any(r['path'] == '/api/products/999' for r in state['requests'])
        for route,title in (('/goods/counts?count_id=71','Count #71'),('/goods/transfers?transfer_id=51','Transfer #51')):
            await page.goto(BASE+route)
            await expect(page.get_by_text(title,exact=True)).to_be_visible()
            await expect(page.get_by_text(LABEL,exact=True)).to_be_visible()
        await page.goto(BASE + '/goods/receiving?product_id=999&location_id=10')
        await expect(page.get_by_text(LABEL, exact=True)).to_be_visible()
        await page.locator('.ant-input-number-input').first.fill('2')
        await page.get_by_role('button', name='Save draft for review', exact=True).click()
        await expect(page.get_by_text('Receipt #31', exact=True)).to_be_visible()
        await expect(page.get_by_text(LABEL, exact=True)).to_be_visible()
        created = [r for r in state['requests'] if r['path'] == '/api/goods/receipts' and r['method'] == 'POST']
        assert len(created) == 1 and created[0]['post_data']['lines'][0]['product_id'] == 999
    finally:
        await context.close()


async def allocation_check(browser, width):
    state = {'mode': 'data', 'api_payload': payload}
    context, page = await context_with_api(browser, state, viewport={'width': width, 'height': 900}, auth={'role': 'manager'})
    try:
        await page.goto(BASE + '/sales/documents/41')
        await page.get_by_role('combobox', name='Line 1 verified product', exact=True).fill('Moon')
        await expect(page.get_by_text(LABEL, exact=True)).to_be_visible()
    finally:
        await context.close()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'vite.log').open('w') as log:
        process = subprocess.Popen(['node', str(FRONTEND / 'node_modules/vite/bin/vite.js'), '--config', str(HERE / 'vite.config.mjs'), '--port', '5190'], cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Vite test server exited')
                try:
                    _, writer = await asyncio.open_connection('127.0.0.1', 5190)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(.1)
            else:
                raise RuntimeError('Vite test server did not start')
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    results = await asyncio.gather(*(check(browser, width) for width in (390, 1440) for check in (receipt_check, allocation_check)), return_exceptions=True)
                    for result in results:
                        if isinstance(result, BaseException):
                            raise result
                finally:
                    await browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
    print('Design label browser checks passed')
