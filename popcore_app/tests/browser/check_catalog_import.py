"""Catalog import and manager Sheet sync regression, using synthetic APIs only."""
import asyncio
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright, expect
from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server
from fixtures import payload as foundation_payload, product

OUT = Path(__file__).resolve().parents[3] / '.local' / 'catalog-import' / 'browser'
PREVIEW = '/api/products/import/preview'
CONFIRM = '/api/products/import/confirm'
HEADERS = ['SKU', '记账名', '产品名称', '系列', '类型', '品牌', '单价', '发售时间', '版本/限量', '渠道', '备注']
VALUES = {'jizhanming': 'Updated catalog name', 'name_cn_en': 'Updated Product',
          'ip_series': 'Foundation', 'product_type': 'Figure', 'brand': 'POPCORE',
          'price': 25, 'release_date': '', 'edition_size': '', 'channel': '', 'notes': 'Catalog note'}
ROWS = [
    {'sku': 'DT-FOUNDATION', 'action': 'update', 'product_id': 1,
     'before': {'sku': 'DT-FOUNDATION', **{key: product().get(key) for key in VALUES}, 'boxes_per_dan': 1}, 'values': VALUES},
    {'sku': 'NEW-001', 'action': 'create', 'product_id': None, 'before': None,
     'values': {**VALUES, 'jizhanming': 'New catalog name', 'name_cn_en': 'New Product'}},
]


def fixture(state):
    def payload(path, query, request):
        if path == '/api/products/1':
            return product()
        if path == '/api/products/search' and state.get('saved'):
            return [{**product(), **row['values'], 'sku': row['sku'], 'id': i + 1} for i, row in enumerate(ROWS)]
        if path == PREVIEW:
            assert request.post_data_json == {'text': state['text']}
            rows = state.get('rows', ROWS)
            return {'rows': rows, 'created': sum(r['action'] == 'create' for r in rows),
                    'updated': sum(r['action'] == 'update' for r in rows)}
        if path == CONFIRM:
            assert request.post_data_json == {'rows': ROWS}
            state['saved'] = True
            return {'ok': True, 'created': 1, 'updated': 1}
        if path == '/api/products/sync-sheet':
            return {'sheet_status': 'ok', 'review': [], 'conflicts': [], 'new_products': [],
                    'ref_learns': [], 'unchanged': 0, 'duplicates': [], 'changed': [{
                        'key': 1, 'ref': '1', 'sheet_jizhanming': 'Sheet name', 'sheet_name': 'Product',
                        'product_id': 1, 'sku': 'DT-FOUNDATION', 'old_jizhanming': 'Old name',
                        'new_jizhanming': 'Sheet name', 'match_via': 'ref', 'score': 100,
                        'prechecked': True, 'candidates': [], 'sheet_ref': '1',
                        'expected_jizhanming': 'Old name', 'expected_sheet_ref': '1',
                        'expected_name_cn_en': 'Product', 'expected_product_type': 'Figure',
                    }]}
        if path == '/api/products/sync-sheet/confirm':
            assert len(request.post_data_json['changes']) == 1
            return {'updated': 1, 'created': 0, 'refs_learned': 0}
        return foundation_payload(path, query, False)
    return payload


async def flow(browser, viewport):
    separator = '\t' if viewport['width'] == 390 else ','
    text = separator.join(HEADERS) + '\n' + '\n'.join(
        separator.join([row['sku']] + [str(value) for value in row['values'].values()]) for row in ROWS)
    state = {'text': text, 'get_status_for_path': {'/api/stock': (403, {'error': 'Stock access unavailable'})}}
    state['api_payload'] = fixture(state)
    context, page = await context_with_api(browser, state, viewport=viewport, auth={'role': 'manager'})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        await page.goto(BASE + '/products')
        await expect(page.get_by_text('DT Foundation Item', exact=True).first).to_be_visible()
        await expect(page.get_by_text('Unknown', exact=True)).to_be_visible()
        sync = page.get_by_role('button', name='Sync from Google Sheet', exact=True)
        await expect(sync).to_be_visible()
        assert await sync.inner_text() == '同步表格 Sync sheet'
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        await page.screenshot(path=OUT / f'catalog-{viewport["width"]}.png')
        await page.get_by_text('DT Foundation Item', exact=True).first.click()
        # Detail: a sheet on phone, a pane beside the table on desktop.
        if viewport['width'] == 390:
            drawer = page.get_by_role('dialog')
            await expect(drawer.get_by_text('Unknown', exact=True)).to_be_visible()
            await drawer.get_by_role('button', name='Close', exact=True).click()
        else:
            pane = page.get_by_role('complementary', name='Product detail')
            await expect(pane.get_by_text('Unknown', exact=True)).to_be_visible()
            await pane.get_by_role('button', name='Close product detail', exact=True).click()
            await expect(pane).to_have_count(0)

        await sync.click()
        dialog = page.get_by_role('dialog')
        await expect(dialog.get_by_text('Sheet name', exact=True)).to_be_visible()
        assert not any(r['path'] == '/api/products/sync-sheet/confirm' for r in state['requests'])
        await dialog.get_by_role('button', name='确认 / Confirm (1)', exact=True).click()
        await expect(dialog).to_have_count(0)

        # File tools live in the 工具 Tools menu.
        await page.get_by_role('button', name='工具 Tools').click()
        await page.get_by_role('menuitem', name='导入目录 Import', exact=True).click()
        dialog = page.get_by_role('dialog', name='Import catalog / 导入产品目录')
        source = dialog.get_by_role('textbox', name='Catalog CSV or TSV')
        await expect(dialog.get_by_text('Required header:', exact=False)).to_be_visible()
        await expect(dialog.get_by_role('spinbutton')).to_have_count(0)
        await expect(dialog.get_by_role('combobox')).to_have_count(0)
        await source.fill(text)
        preview = dialog.get_by_role('button', name='Preview catalog import', exact=True)
        state['status_for_path'] = {PREVIEW: (400, {'error': 'Check import headers'})}
        await preview.click()
        await expect(dialog.get_by_role('alert').filter(has_text='Check import headers')).to_be_visible()
        await expect(source).to_have_value(text)
        state['status_for_path'] = {}
        await preview.click()
        await expect(dialog.get_by_text('Review before applying:', exact=False)).to_be_visible()
        await expect(dialog.get_by_text('Updated Product', exact=False)).to_be_visible()
        await expect(dialog.get_by_text('20', exact=True)).to_be_visible()
        assert not any(r['path'] == CONFIRM for r in state['requests'])
        await page.screenshot(path=OUT / f'preview-{viewport["width"]}.png')
        apply = dialog.get_by_role('button', name='Apply catalog import', exact=True)
        state['status_for_path'] = {CONFIRM: (409, {'error': 'Catalog changed since preview'})}
        await apply.click()
        await expect(dialog.get_by_role('alert').filter(has_text='Catalog changed since preview')).to_be_visible()
        await expect(source).to_have_value(text)
        await expect(apply).to_have_count(0)
        state['status_for_path'] = {}
        await preview.click()
        await expect(apply).to_be_enabled()

        started, release = asyncio.Event(), asyncio.Event()
        async def hold_confirm(route):
            started.set()
            await release.wait()
            await route.fallback()
        await page.route('**' + CONFIRM, hold_confirm)
        await apply.click()
        await asyncio.wait_for(started.wait(), 5)
        await expect(apply).to_be_disabled()
        await expect(dialog.get_by_role('button', name='Edit pasted data')).to_be_disabled()
        release.set()
        await expect(dialog).to_have_count(0)
        await expect(page.get_by_text('New Product', exact=True).first).to_be_visible()
        mutations = [r for r in state['requests'] if r['method'] != 'GET']
        assert all(r['path'].startswith('/api/products/') for r in mutations)
        assert sum(r['path'] == CONFIRM for r in mutations) == 2
        assert not any(r['path'] == '/api/stock/batch_operation' for r in state['requests'])
        # A partial update must display omitted names unchanged, including no deletion mark.
        state['text'] = 'SKU,单价,boxes_per_dan\nDT-FOUNDATION,30,6'
        state['rows'] = [{**ROWS[0], 'values': {'price': 30, 'boxes_per_dan': 6}}]
        await page.get_by_role('button', name='工具 Tools').click()
        await page.get_by_role('menuitem', name='导入目录 Import', exact=True).click()
        await source.fill(state['text'])
        await preview.click()
        row = dialog.get_by_role('row').filter(has_text='DT-FOUNDATION')
        await expect(row.get_by_text('DT Foundation Item', exact=True)).to_have_count(2)
        await expect(row.get_by_text('DT Foundation Item', exact=True).first).to_be_visible()
        assert await row.locator('del').all_text_contents() == ['20', '1']
        await expect(row.get_by_text('每端盒数:', exact=True)).to_be_visible()
        assert not errors, errors
    except Exception:
        await page.screenshot(path=OUT / f'failure-{viewport["width"]}.png', full_page=True)
        raise
    finally:
        await context.close()


async def create_once_and_sort_flow(browser):
    """A weak connection: Create pressed again after a lost response sends the same request key, a
    press while saving sends nothing, and the list sorts by when products were added (their ids)."""
    rows = [{**product(), 'id': pid, 'sku': sku, 'jizhanming': name, 'name_cn_en': name}
            for pid, sku, name in ((1, 'PM0001', 'Oldest box'), (3, 'PM0002', 'Newest box'), (2, 'PM0003', 'Middle box'))]
    state = {'api_payload': lambda path, query, request: rows if path == '/api/products/search'
             else foundation_payload(path, query, False)}
    context, page = await context_with_api(browser, state, viewport={'width': 1440, 'height': 1000}, auth={'role': 'manager'})
    keys, release = [], asyncio.Event()

    async def create(route):
        if route.request.method != 'POST':
            await route.fallback(); return
        keys.append(route.request.headers.get('idempotency-key'))
        if len(keys) == 1:
            await route.abort('failed'); return  # saved on the server, the answer lost on the way
        await release.wait()
        await route.fulfill(status=201, json={'ok': True, 'id': 4, 'sku': 'PM0004'})
    await page.route('**/api/products', create)
    names = page.locator('.pc-prod-name strong')
    sort = page.get_by_role('combobox', name='Sort products')
    try:
        await page.goto(BASE + '/products')
        await expect(names).to_have_text(['Oldest box', 'Newest box', 'Middle box'])
        await sort.click()
        await page.get_by_role('option', name='最新添加 Newest added').click()
        await expect(names).to_have_text(['Newest box', 'Middle box', 'Oldest box'])
        await sort.click()
        await page.get_by_role('option', name='最早添加 Oldest added').click()
        await expect(names).to_have_text(['Oldest box', 'Middle box', 'Newest box'])

        await page.get_by_role('button', name='Add Product').click()
        dialog = page.get_by_role('dialog')
        await dialog.get_by_label('Full Product Name').fill('Pressed twice')
        await dialog.get_by_role('button', name='Create').click()
        await expect(page.get_by_text('Could not reach POPCORE. Press Create again: it will not be added twice.')).to_be_visible()
        await dialog.get_by_role('button', name='Create').click()
        for _ in range(100):
            if len(keys) == 2:
                break
            await asyncio.sleep(.05)
        await dialog.get_by_role('button', name='Create').click()  # still saving: ignored
        await page.wait_for_timeout(300)
        assert len(keys) == 2, keys
        release.set()
        await expect(page.get_by_text('Product created · PM0004')).to_be_visible()
        assert keys[0] and keys[0] == keys[1], keys
    finally:
        await context.close()


async def staff_flow(browser):
    context, page = await context_with_api(browser, {}, auth={'role': 'staff'})
    try:
        await page.goto(BASE + '/products')
        await expect(page.get_by_text('DT Foundation Item', exact=True).first).to_be_visible()
        await expect(page.get_by_role('button', name='Sync from Google Sheet')).to_have_count(0)
        await expect(page.get_by_role('button', name='工具 Tools')).to_have_count(0)
    finally:
        await context.close()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'vite.log').open('w') as log:
        process = subprocess.Popen(['node', str(FRONTEND / 'node_modules/vite/bin/vite.js'),
                                    '--config', str(HERE / 'vite.config.mjs')], cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            await wait_for_server(process)
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    for viewport in ({'width': 1440, 'height': 1000}, {'width': 390, 'height': 844}):
                        await flow(browser, viewport)
                    await staff_flow(browser)
                    await create_once_and_sort_flow(browser)
                finally:
                    await browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
    print('Catalog import browser checks passed (manager desktop/mobile, Sheet preview/confirm, stock 403, CSV/TSV, stale/retry, pending, catalog-only writes, Add product saved once on retry, sort by time added)')


if __name__ == '__main__':
    asyncio.run(main())
