"""Manager opening count-sheet download from Products, using synthetic APIs only."""
import asyncio
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright, expect
from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server

OUT = Path(__file__).resolve().parents[3] / '.local' / 'count-sheet' / 'browser'
SHEET = '/api/products/count-sheet'
# Ant icons prefix their own label to a button's accessible name ("file-text Count sheet").
COUNT_BUTTON = re.compile(r'^(file-text )?Count sheet$')
CSV = 'store_code,location,product_id,sku,name,count,notes\nDT,upstairs,1,DT-FOUNDATION,Foundation Item,,\n'
ERRORS = {
    'inventory_access_denied': (403, 'Inventory access denied',
                                'Your account has no inventory access to MK.'),
    'location_unknown': (400, 'store_code and location must name an active inventory location',
                         'not an active inventory location'),
    'invalid_scope': (400, 'scope must be legacy or catalog', 'Choose legacy stock or the whole catalog'),
}


async def route_sheet(page, sheet):
    async def handle(route):
        query = {k: v[0] for k, v in parse_qs(urlparse(route.request.url).query).items()}
        sheet['queries'].append(query)
        code = sheet.get('error')
        if code:
            status, error, _ = ERRORS[code]
            await route.fulfill(status=status, content_type='application/json',
                                body=json.dumps({'error': error, 'code': code}))
            return
        await route.fulfill(status=200, content_type='text/csv; charset=utf-8', body=CSV)
    await page.route('**' + SHEET + '?*', handle)


async def choose(page, dialog, name, option):
    # The selected-value span sits over the search input; open the select from the input itself.
    await dialog.get_by_role('combobox', name=name, exact=True).click(force=True)
    open_menu = page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)')
    await open_menu.get_by_title(option, exact=True).click()
    await expect(open_menu).to_have_count(0)


async def manager_flow(browser, viewport):
    state, sheet = {}, {'queries': []}
    context, page = await context_with_api(browser, state, viewport=viewport, auth={'role': 'manager'})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    await route_sheet(page, sheet)
    try:
        await page.goto(BASE + '/products')
        await expect(page.get_by_text('DT Foundation Item', exact=True).first).to_be_visible()
        if viewport['width'] == 390:
            await page.get_by_role('button', name='Toggle product filters').click()
        button = page.get_by_role('button', name=COUNT_BUTTON)
        await expect(page.get_by_role('button', name=re.compile(r'^(export )?Export$'))).to_be_visible()
        await button.click()
        dialog = page.get_by_role('dialog', name='Download count sheet')
        await expect(dialog).to_be_visible()
        await expect(dialog.get_by_text('Legacy stock at DT', exact=True)).to_be_visible()
        assert not sheet['queries']
        await expect(page.locator('.ant-modal.ant-zoom-appear, .ant-modal.ant-zoom-enter')).to_have_count(0)
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        await page.screenshot(path=OUT / f'dialog-{viewport["width"]}.png')

        # Default: selected store DT, floor, legacy scope.
        async with page.expect_download() as info:
            await dialog.get_by_role('button', name='Download count sheet').click()
        download = await info.value
        assert download.suggested_filename.startswith('count_sheet_DT_floor_legacy_'), download.suggested_filename
        assert Path(await download.path()).read_text(encoding='utf-8') == CSV
        assert sheet['queries'][-1] == {'store_code': 'DT', 'location': 'floor', 'scope': 'legacy'}, sheet['queries']
        await expect(dialog).to_be_hidden()

        await button.click()
        await choose(page, dialog, 'Count sheet location', 'MK · Warehouse')
        await expect(dialog.get_by_text('Legacy stock at MK', exact=True)).to_be_visible()
        await choose(page, dialog, 'Count sheet scope', 'Whole catalog')
        async with page.expect_download() as info:
            await dialog.get_by_role('button', name='Download count sheet').click()
        download = await info.value
        assert download.suggested_filename.startswith('count_sheet_MK_warehouse_catalog_'), download.suggested_filename
        assert sheet['queries'][-1] == {'store_code': 'MK', 'location': 'warehouse', 'scope': 'catalog'}
        await expect(dialog).to_be_hidden()

        for code, (_, error, hint) in ERRORS.items():
            sheet['error'] = code
            await button.click()
            await dialog.get_by_role('button', name='Download count sheet').click()
            alert = dialog.get_by_role('alert')
            await expect(alert.get_by_text(error, exact=True)).to_be_visible()
            await expect(alert.get_by_text(hint, exact=False)).to_be_visible()
            await expect(page.get_by_text('Count sheet download failed', exact=False)).to_have_count(0)
            await expect(dialog).to_be_visible()
            if code == 'inventory_access_denied':
                await page.screenshot(path=OUT / f'denied-{viewport["width"]}.png')
            await dialog.get_by_role('button', name='Cancel', exact=True).click()
            await expect(dialog).to_be_hidden()
        # Reopening clears the previous error.
        sheet.pop('error')
        await button.click()
        await expect(dialog.get_by_role('alert')).to_have_count(0)

        mutations = [r for r in state['requests'] if r['method'] != 'GET']
        assert not mutations, mutations
        assert not errors, errors
    except Exception:
        await page.screenshot(path=OUT / f'failure-{viewport["width"]}.png', full_page=True)
        raise
    finally:
        await context.close()


async def staff_flow(browser):
    context, page = await context_with_api(browser, {}, auth={'role': 'staff'})
    try:
        await page.goto(BASE + '/products')
        await expect(page.get_by_text('DT Foundation Item', exact=True).first).to_be_visible()
        await expect(page.get_by_role('button', name=COUNT_BUTTON)).to_have_count(0)
        await expect(page.get_by_role('dialog', name='Download count sheet')).to_have_count(0)
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
                        await manager_flow(browser, viewport)
                    await staff_flow(browser)
                finally:
                    await browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
    print('Count sheet browser checks passed (manager desktop/mobile, default and chosen location/scope, '
          'CSV download, 403/400 messages, staff hidden, no mutations)')


if __name__ == '__main__':
    asyncio.run(main())
