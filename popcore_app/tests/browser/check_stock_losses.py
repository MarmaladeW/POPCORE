"""损耗 Loss page and the 库存 stock value, isolated fixtures: managers record, undo and clear the damaged pile."""
import asyncio
import re
import subprocess
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT, context_with_api
from fixtures import payload as base_payload

PORT = 5198
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'stock-losses'
LOCATIONS = [{'id': 1, 'code': 'floor', 'name': 'Floor'}, {'id': 2, 'code': 'upstairs', 'name': 'Upstairs'}]
RECORD = {'id': 7, 'business_date': '2026-11-03', 'created_at': '2026-11-03 18:00:00', 'product_id': 41, 'name': 'Smiski bath', 'sku': 'DR0013',
          'unit': 'box', 'quantity': 2, 'reason': 'broken', 'note': 'dropped while restocking', 'location_id': 1, 'location_code': 'floor',
          'location_name': 'Floor', 'unit_price_cents': 1799, 'value_cents': 3598, 'actor_name': 'Belle', 'undone': False,
          'undone_at': None, 'undone_by_name': None}
TOTALS = {'broken': {'units': 2, 'value_cents': 3598, 'unpriced_units': 0, 'records': 1},
          'stolen': {'units': 0, 'value_cents': 0, 'unpriced_units': 0, 'records': 0},
          'missing': {'units': 0, 'value_cents': 0, 'unpriced_units': 0, 'records': 0},
          'other': {'units': 0, 'value_cents': 0, 'unpriced_units': 0, 'records': 0}}
PILE = [{'product_id': 41, 'name': 'Smiski bath', 'sku': 'DR0013', 'unit': 'box', 'location_id': 1, 'location_code': 'floor',
         'location_name': 'Floor', 'quantity': 2, 'version': 3, 'price_cents': 1799, 'value_cents': 3598}]
FOUND = [{'product_id': 41, 'name': 'Smiski bath', 'sku': 'DR0013', 'unit': 'box', 'stock_form': 'random_box', 'price_cents': 1799,
          'places': [{'location_id': 1, 'code': 'floor', 'name': 'Floor', 'available': 40, 'version': 5},
                     {'location_id': 2, 'code': 'upstairs', 'name': 'Upstairs', 'available': 9, 'version': 2}]}]


def payload(path, query, request):
    if path == '/api/losses' and request.method == 'GET':
        return {'live': True, 'store': {'id': 1, 'code': 'DT', 'name': 'Downtown', 'live': True}, 'month': '2026-11', 'today': '2026-11-03',
                'locations': LOCATIONS, 'records': [RECORD], 'totals': TOTALS, 'units': 2, 'value_cents': 3598, 'unpriced_units': 0, 'pile': PILE}
    if path == '/api/losses' and request.method == 'POST':
        return {'loss': {**RECORD, 'id': 8, 'reason': 'stolen', 'quantity': 3, 'value_cents': 5397}}
    if path == '/api/losses/search':
        return {'results': FOUND if 'q=smi' in query else []}
    if path == '/api/losses/pile':
        return {'document_id': 90, 'action': 'restore', 'quantity': 2}
    if re.fullmatch(r'/api/losses/\d+/undo', path):
        return {'loss': {**RECORD, 'undone': True}}
    if path == '/api/inventory/overview':
        return {'live': True, 'stores': ['DT'], 'today': '2026-11-03', 'in_stock_products': 1273, 'report_days': 3, 'last_report': '2026-10-31',
                'figures': [{'key': 'floor', 'codes': ['floor'], 'units': 7495, 'products': 512, 'by_unit': {'box': 6640, 'piece': 855},
                             'value_cents': 24092560, 'unpriced_units': 21},
                            {'key': 'back', 'codes': ['upstairs'], 'units': 4178, 'products': 655, 'by_unit': {'set': 1783, 'piece': 2386, 'box': 9},
                             'value_cents': 64753386, 'unpriced_units': 147}],
                'refill': [], 'refill_count': 0, 'running_low': [], 'sold_out': [], 'best_sellers': [],
                'recent_changes': [{'business_date': '2026-11-03', 'kind': 'loss', 'store_code': 'DT', 'added': 2, 'removed': 5, 'saleable': -5, 'documents': 2}],
                'not_set_up': 0,
                'value': {'value_cents': 88845946, 'unpriced_units': 168, 'unpriced_products': 88}}
    if path == '/api/inventory/series':
        return {'mode': 'authoritative', 'locations': [], 'unassigned_count': 0, 'series': [], 'products': []}
    if path == '/api/inventory/locations':
        return [{**location, 'store_id': 1, 'store_code': 'DT', 'opening_verified': True} for location in LOCATIONS]
    return base_payload(path, query)


async def eventually(read, expected, timeout=5):
    for _ in range(int(timeout * 10)):
        if read() == expected:
            return
        await asyncio.sleep(.1)
    raise AssertionError(f'{read()!r} != {expected!r}')


async def check(browser, width):
    state = {'mode': 'data', 'api_payload': payload}
    context, page = await context_with_api(browser, state, viewport={'width': width, 'height': 900}, auth={'role': 'manager'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    # The 库存 overview shows the stock's retail value to managers, and a loss in recent changes.
    await page.goto(BASE + '/stock')
    overview = page.get_by_role('region', name='库存概况 Inventory overview', exact=True)
    await expect(overview.get_by_text('$888,459', exact=True)).to_be_visible()
    await expect(overview.get_by_text('At retail · 168 without a price', exact=True)).to_be_visible()
    # Each place's worth sits under its units.
    await expect(overview.get_by_text('价值 $240,926', exact=True)).to_be_visible()
    await expect(overview.get_by_text('价值 $647,534', exact=True)).to_be_visible()
    await expect(overview.get_by_text('−5', exact=True)).to_be_visible()
    await page.get_by_role('link', name=re.compile('损耗')).first.click()
    await expect(page).to_have_url(re.compile(r'/stock/loss$'))

    await expect(page.get_by_text('$35.98', exact=True).first).to_be_visible()
    pile = page.get_by_role('region', name='损坏待处理 Damaged pile', exact=True)
    await expect(pile.get_by_text('Smiski bath', exact=True)).to_be_visible()
    records = page.get_by_role('region', name='损耗记录 Loss records', exact=True)
    await expect(records.get_by_text('Floor · dropped while restocking · by Belle', exact=False)).to_be_visible()

    # Record: search, pick, where, what happened, how many; the effect line says what will change.
    await page.get_by_label('Search a product with stock here', exact=True).fill('smiski')
    await page.get_by_role('button', name=re.compile('^Smiski bath')).click()
    record = page.get_by_role('button', name=re.compile('记录损耗'))
    await expect(record).to_be_disabled()
    await page.get_by_role('button', name=re.compile('^楼上 Upstairs')).click()
    await page.get_by_role('button', name=re.compile('^被偷 Stolen')).click()
    await page.get_by_role('button', name='One more', exact=True).click()
    await page.get_by_role('button', name='One more', exact=True).click()
    await expect(page.get_by_text('楼上 9 → 6 · 3 盒 leave stock', exact=False)).to_be_visible()
    await page.get_by_role('button', name=re.compile('^其他 Other')).click()
    await expect(record).to_be_disabled()  # 其他 needs a note
    await page.get_by_label('备注 Note', exact=True).fill('given away')
    await page.get_by_role('button', name=re.compile('^被偷 Stolen')).click()
    await page.get_by_label('备注 Note', exact=True).fill('seen on camera')
    if width >= 768:
        await page.screenshot(path=OUT / f'card-{width}.png', full_page=True, animations='disabled')
    await record.click()
    await expect(page.get_by_role('status').filter(has_text='已记录 Recorded')).to_be_visible()
    posted = [item for item in state['requests'] if item['path'] == '/api/losses' and item['method'] == 'POST']
    assert len(posted) == 1 and posted[0]['idempotency_key'], posted
    assert posted[0]['post_data'] == {'store_code': 'DT', 'product_id': 41, 'location_id': 2, 'quantity': 3, 'reason': 'stolen',
                                      'expected_version': 2, 'note': 'seen on camera'}, posted[0]['post_data']

    # Undo a record and put the damaged pile back on sale.
    await records.get_by_role('button', name=re.compile('撤销')).click()
    await page.locator('.ant-popover').get_by_role('button', name=re.compile('撤销')).click()
    await eventually(lambda: '/api/losses/7/undo' in [item['path'] for item in state['requests'] if item['method'] == 'POST'], True)
    await pile.get_by_role('button', name=re.compile('放回可售')).click()
    await eventually(lambda: [item.get('post_data') for item in state['requests'] if item['path'] == '/api/losses/pile'],
                     [{'store_code': 'DT', 'product_id': 41, 'location_id': 1, 'quantity': 2, 'action': 'restore', 'expected_version': 3}])
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'horizontal overflow at {width}px'
    await page.screenshot(path=OUT / f'losses-{width}.png', full_page=True, animations='disabled')
    await context.close()

    # Staff see no 损耗 tool and cannot open the page.
    context, page = await context_with_api(browser, {'mode': 'data', 'api_payload': payload}, viewport={'width': width, 'height': 900}, auth={'role': 'staff'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE + '/stock')
    await expect(page.get_by_role('link', name=re.compile('^盘点')).first).to_be_visible()
    await expect(page.get_by_role('link', name=re.compile('损耗'))).to_have_count(0)
    await page.goto(BASE + '/stock/loss')
    await expect(page.get_by_role('button', name=re.compile('记录损耗'))).to_have_count(0)
    await context.close()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'vite.log').open('w') as log:
        process = subprocess.Popen(['node', str(FRONTEND / 'node_modules/vite/bin/vite.js'), '--config', str(HERE / 'vite.config.mjs'), '--port', str(PORT)],
                                   cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError('Vite test server exited')
                try:
                    _, writer = await asyncio.open_connection('127.0.0.1', PORT); writer.close(); await writer.wait_closed(); break
                except OSError: await asyncio.sleep(.1)
            else: raise RuntimeError('Vite test server did not start')
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    for width in (390, 1280): await check(browser, width)
                finally: await browser.close()
        finally:
            process.terminate(); process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
    print('PASS 损耗 stock value, record, undo, damaged pile and staff refusal, 390/1280px')
