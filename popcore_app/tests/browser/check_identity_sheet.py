"""Real Flask identity sheet export/import with disposable SQLite and mocked auth."""
import asyncio
import csv
import io
import json
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect
from werkzeug.serving import make_server
from check_foundation import FRONTEND, HERE

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / '.local/identity-sheet/browser'
API_PORT, VITE_PORT = 5062, 5197
BASE = f'http://127.0.0.1:{VITE_PORT}'
CONFIRM = '/api/products/identity-import/confirm'
sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
from test_identity_sheet import IdentitySheetTests

STOCK_TABLES = ('stock', 'stock_transactions', 'stock_movements', 'inventory_balances',
                'inventory_documents', 'inventory_movements')


def edited_sheet(downloaded):
    """Fill the downloaded DT sheet the way a manager would in a spreadsheet."""
    rows = list(csv.DictReader(io.StringIO(downloaded.lstrip('﻿'))))
    assert {row['SKU'] for row in rows} == {'TEST-1', 'LOCKED-1'}, rows
    for row in rows:
        if row['SKU'] == 'TEST-1':
            row.update(series='Fresh Series', stock_form='ordinary')
        else:
            row.update(stock_form='ordinary', stock_unit='')  # locked by its stock history
    rows.append({**dict.fromkeys(rows[0], ''), 'SKU': 'BOX-1', 'series': 'Existing Series',
                 'stock_form': 'random_box'})
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


async def open_page(browser, viewport, role):
    context = await browser.new_context(viewport=viewport, accept_downloads=True)
    await context.add_init_script(
        f"window.__FOUNDATION_AUTH={{role:'{role}',token:'{role}'}};"
        "localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    page = await context.new_page()

    async def local_only(route):
        if urlsplit(route.request.url).hostname not in ('127.0.0.1', 'localhost'):
            await route.abort()
        else:
            await route.continue_()
    await page.route('**/*', local_only)
    return context, page


async def manager_flow(browser, fixture, viewport):
    width = viewport['width']
    context, page = await open_page(browser, viewport, 'manager')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    confirms = []

    async def lose_first_response(route):
        confirms.append(route.request.headers.get('idempotency-key'))
        response = await route.fetch()
        if len(confirms) == 1:
            assert response.status == 200, await response.text()
            await route.abort('failed')  # Saved on the server, response lost on the way back.
        else:
            await route.fulfill(response=response)
    await page.route('**' + CONFIRM, lose_first_response)
    try:
        await page.goto(BASE + '/products')
        await expect(page.get_by_text('TEST-1', exact=True).first).to_be_visible()
        tools = page.get_by_role('button', name='工具 Tools')
        await tools.click()
        export_button = page.get_by_role('menuitem', name='导出身份表 Export identity sheet', exact=True)
        import_button = page.get_by_role('menuitem', name='导入身份表 Import identity sheet', exact=True)
        await expect(export_button).to_be_visible()
        await expect(import_button).to_be_visible()
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')

        await export_button.click()
        dialog = page.get_by_role('dialog', name='Export identity sheet')
        await dialog.locator('.ant-select').click()
        await page.get_by_title('With legacy stock at DT (Downtown Toronto)').click()
        await expect(dialog.get_by_role('checkbox', name='Unverified products only')).to_be_checked()
        async with page.expect_download() as info:
            await dialog.get_by_role('button', name='Download identity sheet').click()
        download = await info.value
        assert download.suggested_filename.startswith('identity_sheet_DT_unverified_'), download.suggested_filename
        sheet = edited_sheet(Path(await download.path()).read_text(encoding='utf-8'))
        await expect(dialog).to_be_hidden()

        stock_before = fixture.snapshot(STOCK_TABLES)
        await tools.click()
        await import_button.click()
        dialog = page.get_by_role('dialog', name='Import identity sheet')
        source = dialog.get_by_role('textbox', name='Identity sheet CSV or TSV')
        await dialog.get_by_label('Identity sheet file').set_input_files(
            files=[{'name': 'identity.csv', 'mimeType': 'text/csv', 'buffer': sheet.encode('utf-8')}])
        await expect(source).to_have_value(sheet)
        await dialog.get_by_role('button', name='Preview identity import').click()
        await expect(dialog.get_by_text('2 to verify · 0 unchanged · 0 skipped · 1 rejected', exact=False)).to_be_visible()
        await expect(dialog.get_by_text('Creates 1 new series: Fresh Series', exact=True)).to_be_visible()
        # Rejected rows are shown first, with their reason.
        reason = dialog.get_by_text('Referenced product identity cannot be changed in place')
        await reason.scroll_into_view_if_needed()
        await expect(reason).to_be_in_viewport(ratio=.99)
        await page.screenshot(path=OUT / f'preview-rejected-{width}.png')
        await dialog.get_by_text('Verify (2)', exact=True).click()
        rows = dialog.locator('tr.ant-table-row, .pc-identity-row')
        await expect(rows).to_have_count(2)
        await expect(rows.filter(has_text='TEST-1')).to_contain_text('→ Fresh Series (new) · ordinary · piece')
        await expect(rows.filter(has_text='BOX-1')).to_contain_text('→ Existing Series · random_box · box')
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
        await page.screenshot(path=OUT / f'preview-{width}.png')
        assert fixture.identity(fixture.product_id)[-1] == 'unverified'

        await dialog.get_by_role('button', name='Verify 2 products').click()
        await expect(dialog.get_by_role('alert').filter(has_text='Retry to check its result')).to_be_visible()
        await dialog.get_by_role('button', name='Retry', exact=True).click()
        await expect(dialog).to_be_hidden()
        await expect(page.get_by_text('Identity verified for 2 products, 1 series created')).to_be_visible()
        assert len(confirms) == 2 and confirms[0] and confirms[0] == confirms[1], confirms
        assert fixture.identity(fixture.product_id) == ('Fresh Series', 'ordinary', 'piece', None, 'verified')
        assert fixture.identity(fixture.box_id) == ('Existing Series', 'random_box', 'box', None, 'verified')
        assert fixture.identity(fixture.locked_id) == (None, 'random_box', 'box', None, 'unverified')
        assert fixture.snapshot(STOCK_TABLES) == stock_before
        with closing(fixture.connect()) as con:
            assert con.execute("SELECT COUNT(*) FROM product_series WHERE name='Fresh Series'").fetchone()[0] == 1
        assert not errors, errors
    except Exception:
        await page.screenshot(path=OUT / f'failure-{width}.png', timeout=10000)
        raise
    finally:
        await context.close()


async def staff_flow(browser):
    context, page = await open_page(browser, {'width': 1440, 'height': 1000}, 'staff')
    try:
        await page.goto(BASE + '/products')
        await expect(page.get_by_text('TEST-1', exact=True).first).to_be_visible()
        await expect(page.get_by_role('button', name='工具 Tools')).to_have_count(0)
    finally:
        await context.close()


async def wait_for_vite(process):
    for _ in range(150):
        if process.poll() is not None:
            raise RuntimeError('Vite test server exited')
        try:
            _, writer = await asyncio.open_connection('127.0.0.1', VITE_PORT)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(.1)
    raise RuntimeError('Vite test server did not start')


def serve(fixture):
    server = make_server('127.0.0.1', API_PORT, fixture.app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def fresh_fixture():
    fixture = IdentitySheetTests()
    fixture.setUp()
    return fixture


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    config = OUT / 'vite.mjs'
    config.write_text(f"import config from {json.dumps(str(HERE / 'vite.config.mjs'))}; export default "
                      f"{{...config,server:{{...config.server,port:{VITE_PORT},proxy:{{'/api':'http://127.0.0.1:{API_PORT}'}}}}}}")
    with (OUT / 'vite.log').open('w') as log:
        process = subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--config', str(config)],
                                   cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            await wait_for_vite(process)
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                try:
                    for viewport in ({'width': 1440, 'height': 1000}, {'width': 390, 'height': 844}):
                        fixture = fresh_fixture()  # Each viewport starts from the same disposable data.
                        server, thread = serve(fixture)
                        try:
                            await manager_flow(browser, fixture, viewport)
                            if viewport['width'] == 1440:
                                await staff_flow(browser)
                        finally:
                            server.shutdown()
                            server.server_close()
                            thread.join(timeout=5)
                            fixture.tearDown()
                            fixture.doCleanups()
                finally:
                    await browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
    print('PASS: identity sheet export (DT, unverified) → file upload → preview with rejected reason and new series '
          '→ lost-response retry with one key → verified identities, stock untouched; desktop/mobile; staff hidden.')
