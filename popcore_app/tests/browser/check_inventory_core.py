from __future__ import annotations

import asyncio
import json
import re
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
OUT = ROOT / '.local' / 'build-2' / 'browser'


PRODUCTS = [
    {'id': 1, 'product_id': 1, 'sku': 'LEGACY-1', 'jizhanming': 'Legacy Item',
     'name_cn_en': 'Legacy Item', 'price': None, 'ip_series': 'Core',
     'product_type': '盲盒', 'boxes_per_dan': 12,
     'stock_form': None, 'stock_unit': None, 'design_name': None,
     'identity_status': 'unverified', 'upstairs_qty': 0, 'instore_qty': 0},
    {'id': 2, 'product_id': 2, 'sku': 'RANDOM-1', 'jizhanming': 'Random Box',
     'name_cn_en': 'Random Box', 'price': 18, 'ip_series': 'Core',
     'product_type': '盲盒', 'boxes_per_dan': 12,
     'stock_form': 'random_box', 'stock_unit': 'box', 'design_name': None,
     'identity_status': 'verified', 'upstairs_qty': 5, 'instore_qty': 3},
    {'id': 3, 'product_id': 3, 'sku': 'SET-1', 'jizhanming': 'Sealed Set',
     'name_cn_en': 'Sealed Set', 'price': 216, 'ip_series': 'Core',
     'product_type': '盲盒', 'boxes_per_dan': 12,
     'stock_form': 'sealed_set', 'stock_unit': 'set', 'design_name': None,
     'identity_status': 'verified', 'upstairs_qty': 1, 'instore_qty': 0},
    {'id': 4, 'product_id': 4, 'sku': 'DESIGN-A', 'jizhanming': 'Design A',
     'name_cn_en': 'Design A', 'price': 18, 'ip_series': 'Core',
     'product_type': '盲盒', 'boxes_per_dan': 1,
     'stock_form': 'confirmed_design', 'stock_unit': 'box', 'design_name': 'A',
     'identity_status': 'verified', 'upstairs_qty': 1, 'instore_qty': 0},
    {'id': 5, 'product_id': 5, 'sku': 'DESIGN-B', 'jizhanming': 'Design B',
     'name_cn_en': 'Design B', 'price': 18, 'ip_series': 'Core',
     'product_type': '盲盒', 'boxes_per_dan': 1,
     'stock_form': 'confirmed_design', 'stock_unit': 'box', 'design_name': 'B',
     'identity_status': 'verified', 'upstairs_qty': 1, 'instore_qty': 0},
]
FAR_PRODUCT = {**PRODUCTS[1], 'id': 501, 'product_id': 501, 'sku': 'FAR-501',
               'jizhanming': 'Outside First 500', 'name_cn_en': '第501项 / Outside First 500'}


def api_payload(path, query, request):
    if path == '/api/products/search':
        if parse_qs(query).get('q', [''])[0].lower() == 'far-501':
            return [FAR_PRODUCT]
        return PRODUCTS
    if path == '/api/stock':
        return {'items': PRODUCTS, 'total': len(PRODUCTS)} if 'page=' in query else PRODUCTS
    if path == '/api/stock/summary':
        return {'products_tracked': 5, 'total_upstairs_qty': 8,
                'total_instore_qty': 3, 'low_stock_count': 4,
                'out_of_stock_count': 1, 'total_stock_value': 0,
                'mode': 'authoritative', 'complete': True,
                'unit_totals': [{'unit': 'box', 'floor_qty': 3, 'back_qty': 5,
                                 'total_qty': 8}]}
    match = re.fullmatch(r'/api/products/(\d+)', path)
    if match:
        item = next(p for p in [*PRODUCTS, FAR_PRODUCT] if p['id'] == int(match.group(1)))
        return {**item, 'aliases': [], 'barcodes': (
            [{'id': 1, 'code': '001234567890', 'code_kind': 'manufacturer',
              'input_unit': 'box', 'quantity_per_scan': 1}]
            if item['id'] in (4, 5) else []),
            'conversions': ([{'id': 1, 'target_product_id': 2,
                              'target_sku': 'RANDOM-1', 'output_per_input': 12,
                              'version': 1}] if item['id'] == 3 else [])}
    if re.fullmatch(r'/api/products/\d+/inventory-identity', path):
        product_id = int(path.split('/')[3])
        item = next(p for p in PRODUCTS if p['id'] == product_id)
        return {**item, 'barcodes': [], 'conversions': []}
    if path == '/api/product-series':
        return [{'id': 1, 'name': 'Core'}]
    if path == '/api/inventory/locations':
        return [
            {'id': 10, 'store_id': 1, 'store_code': 'DT', 'code': 'floor',
             'name': 'Floor', 'is_active': True, 'opening_verified': True},
            {'id': 11, 'store_id': 1, 'store_code': 'DT', 'code': 'upstairs',
             'name': 'Upstairs', 'is_active': True, 'opening_verified': True},
        ]
    if path == '/api/inventory/balances':
        return {'store_code': 'DT', 'mode': 'authoritative', 'items': [
            {'product_id': 2, 'location_id': 10, 'disposition': 'saleable',
             'quantity': 3, 'version': 1, 'unit': 'box', 'opening_verified': True},
            {'product_id': 2, 'location_id': 11, 'disposition': 'saleable',
             'quantity': 5, 'version': 1, 'unit': 'box', 'opening_verified': True},
        ]}
    if path == '/api/inventory/resolve-barcode':
        return {'status': 'ambiguous', 'code': '001234567890',
                'candidates': [PRODUCTS[3], PRODUCTS[4]],
                'requires_selection': True}
    if path == '/api/products/match':
        return {'results': [{'query': 'Random Box', 'status': 'matched',
                             'candidates': [PRODUCTS[1]]}]}
    return foundation_payload(path, query, False)


async def checks(browser):
    evidence = ROOT / '.local' / 'frontend-alignment' / 'after'
    evidence.mkdir(parents=True, exist_ok=True)
    opening_commands = []
    def tracked_api(path, query, request):
        if path == '/api/inventory/commands' and request.method == 'POST':
            opening_commands.append(request.post_data)
        return api_payload(path, query, request)
    state = {'mode': 'data', 'api_payload': tracked_api}
    context, page = await context_with_api(browser, state)
    await page.goto(BASE + '/products')
    await expect(page.get_by_text('Legacy Item', exact=True).first).to_be_visible()
    await expect(page.get_by_text('Unverified', exact=True).first).to_be_visible()
    await expect(page.get_by_text('Verified', exact=True).first).to_be_visible()
    await page.screenshot(path=evidence / 'products-1280.png', full_page=True)
    await page.get_by_text('Sealed Set', exact=True).last.click()
    await expect(page.get_by_text('Conversion v1', exact=False)).to_be_visible()
    await page.keyboard.press('Escape')
    search = page.get_by_placeholder('Search name, SKU, 记账名...')
    await search.fill('FAR-501')
    await expect(page.get_by_text('Outside First 500', exact=True).first).to_be_visible()
    await page.get_by_text('Outside First 500', exact=True).first.click()
    await expect(page.get_by_text('第501项 / Outside First 500', exact=True)).to_be_visible()
    await page.keyboard.press('Escape')

    resolution = await page.evaluate("""async () => {
      const response = await fetch('/api/inventory/resolve-barcode?code=001234567890&purpose=confirmed',
        {headers: {Authorization: 'Bearer fixture-token'}})
      return response.json()
    }""")
    assert resolution['status'] == 'ambiguous'
    assert resolution['requires_selection'] is True
    assert len(resolution['candidates']) == 2

    await page.goto(BASE + '/stock')
    await page.locator('select').first.select_option('DT')
    await expect(page.get_by_role('heading', name='Inventory', exact=True)).to_be_visible()
    await expect(page.get_by_text('Authoritative inventory', exact=True)).to_be_visible()
    await expect(page.get_by_text('Floor · saleable: 3 boxes', exact=True).first).to_be_visible()
    await page.screenshot(path=evidence / 'inventory-1280.png', full_page=True)
    row = page.get_by_role('row').filter(has_text='Random Box')
    await row.get_by_role('button', name='Adjust').click()
    await expect(page.get_by_text('Quantity (box)', exact=True)).to_be_visible()
    quantity = page.locator('.ant-input-number-input').last
    await quantity.fill('2')
    await expect(page.get_by_text('Effect before posting', exact=True)).to_be_visible()
    await expect(page.get_by_text(re.compile(r'2 box.*Downtown'))).to_be_visible()
    await page.keyboard.press('Escape')

    await page.get_by_role('button', name='Batch Import').click()
    await page.locator('textarea').fill('Random Box 12*1')
    await page.get_by_role('button', name='匹配产品').click()
    await expect(page.get_by_text('boxes', exact=True)).to_be_visible()
    batch_row = page.get_by_role('row').filter(has_text='Random Box')
    await expect(batch_row.locator('.ant-input-number-input')).to_have_value('12')
    assert opening_commands == []
    await context.close()

    def legacy_api(path, query, request):
        if path == '/api/stock/summary':
            return {**api_payload(path, query, request), 'mode': 'legacy', 'complete': False}
        return api_payload(path, query, request)
    context,page=await context_with_api(browser,{
        'mode':'data','api_payload':legacy_api,
        'get_status_for_path':{
            '/api/inventory/locations':(403,{'error':'Inventory access denied'}),
            '/api/inventory/balances':(403,{'error':'Inventory access denied'}),
        },
    })
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE+'/stock')
    await expect(page.get_by_text('Legacy inventory view',exact=True)).to_be_visible()
    await context.close()

    def unopened_api(path, query, request):
        result = api_payload(path, query, request)
        if path == '/api/inventory/locations':
            return [{**location, 'opening_verified': False} for location in result]
        return result
    context,page=await context_with_api(browser,{'mode':'data','api_payload':unopened_api})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE+'/stock')
    await expect(page.get_by_text('Opening review incomplete',exact=True)).to_be_visible()
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
                await checks(browser)
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
    print(f'Inventory core browser checks passed in {time.perf_counter() - started:.1f}s', flush=True)
