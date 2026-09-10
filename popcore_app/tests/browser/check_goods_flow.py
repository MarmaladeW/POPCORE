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
        if '00123' in query:
            return {'status': 'exact', 'candidates': [{
                'product_id': 1, 'stock_unit': 'piece', 'quantity_per_scan': 1,
            }]}
        return {'status': 'unknown', 'candidates': []}
    if path == '/api/goods/receipts':
        return {'id': 31, 'version': 1, 'status': 'draft'}
    if path == '/api/goods/receipts/31/post':
        return {'id': 31, 'version': 2, 'status': 'posted', 'inventory_document_id': 41}
    if path == '/api/goods/transfers':
        return {'id': 51, 'version': 1, 'status': 'planned'}
    if path == '/api/goods/transfers/51/dispatch':
        return {'id': 51, 'version': 2, 'status': 'active', 'inventory_document_id': 61}
    if path == '/api/goods/transfers/51/receive':
        return {'id': 51, 'version': 3, 'status': 'completed', 'inventory_document_id': 62}
    if path == '/api/goods/counts':
        return {'id': 71, 'version': 1, 'status': 'draft'}
    if path == '/api/goods/counts/71/submit':
        return {'id': 71, 'version': 2, 'status': 'submitted'}
    if path == '/api/goods/counts/71/approve':
        return {'id': 71, 'version': 3, 'status': 'approved', 'inventory_document_id': 81}
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
    await scan.fill('00123')
    await scan.press('Enter')
    await expect(page.get_by_text('+1 piece', exact=False)).to_be_visible()
    await scan.fill('00123')
    await scan.press('Enter')
    await expect(page.get_by_text('+2 piece', exact=False)).to_be_visible()
    await page.get_by_role('button', name='Review and post receipt').click()

    await page.goto(BASE + '/goods/transfers')
    await expect(page.get_by_text('Transfer stock', exact=True)).to_be_visible()
    await page.goto(BASE + '/goods/counts')
    await expect(page.get_by_text('Physical count', exact=True)).to_be_visible()
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
