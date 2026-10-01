"""Manager history corrections and source-separated insights on disposable Flask data."""
import asyncio
import json
import subprocess
import sys
from contextlib import closing
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT, context_with_api

BASE = 'http://127.0.0.1:5196'
OUT = ROOT / '.local' / 'store-insights-matching'
MATCH_URL = BASE + '/sales/matching?from=2026-09-01&to=2026-09-30'
OVERVIEW_URL = BASE + '/reports?from=2026-09-01&to=2026-09-03'
sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
from test_sales_history_matching import SalesHistoryMatchingTests
from test_store_insights import StoreOverviewTests


async def mount(browser, fixture, width, prefixes):
    state = {'posts': [], 'lose_response': False, 'forced_get': None}
    context, page = await context_with_api(browser, {}, viewport={'width': width, 'height': 1000}, auth={'role': 'manager'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown'}))")
    async def real_api(route):
        url = urlsplit(route.request.url)
        if not any(url.path.startswith(prefix) for prefix in prefixes):
            return await route.fallback()
        if route.request.method == 'GET' and state['forced_get']:
            status, message = state['forced_get']
            return await route.fulfill(status=status, content_type='application/json', body=json.dumps({'error': message}))
        body = route.request.post_data_json if route.request.method == 'POST' else None
        key = route.request.headers.get('idempotency-key', '')
        response = fixture.client.open(url.path + ('?' + url.query if url.query else ''), method=route.request.method,
            headers={**fixture.headers('manager'), 'Idempotency-Key': key}, json=body)
        if body is not None:
            state['posts'].append({'body': body, 'key': key, 'status': response.status_code})
            if state['lose_response'] and response.status_code == 200:
                state['lose_response'] = False
                return await route.fulfill(status=503, content_type='application/json', body='{"error":"Correction result unconfirmed"}')
        await route.fulfill(status=response.status_code, content_type=response.content_type, body=response.get_data())
    await page.route('**/api/**', real_api)
    return context, page, state


async def check_layout(page):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    dialog = page.get_by_role('dialog')
    if await dialog.count():
        assert await dialog.evaluate('element => element.scrollWidth <= element.clientWidth')
        await expect(dialog).to_be_in_viewport(ratio=.99)


async def choose_target(page):
    await page.get_by_role('combobox', name='Search another product', exact=True).fill('TARGET')
    await page.get_by_text('Correct design · TARGET', exact=True).click()
    await expect(page.locator('input[type=radio]:checked')).to_have_count(1)


async def prepare_review(page):
    await page.get_by_role('button', name='Review row', exact=True).click()
    await expect(page.get_by_role('dialog', name='Review historical product match', exact=True)).to_be_visible()
    await expect(page.locator('input[type=radio]:checked')).to_have_count(0)
    await expect(page.get_by_role('button', name='Save product correction', exact=True)).to_be_disabled()


async def matching_retry_checks(browser, width):
    fixture = SalesHistoryMatchingTests(); fixture.setUp(); context = None
    try:
        with closing(fixture.connect()) as con:
            con.execute("UPDATE products SET search_blob='target correct design' WHERE id=?", (fixture.target,))
            con.execute('UPDATE daily_sales SET unit_price=NULL WHERE id=?', (fixture.record,)); con.commit()
        before = fixture.row()
        protected = fixture.snapshot(('stock', 'stock_transactions', 'inventory_documents', 'inventory_movements', 'product_aliases', 'report_match_choices'))
        context, page, state = await mount(browser, fixture, width, ('/api/sales/history-matches', '/api/sales/record/', '/api/products/search'))
        await page.goto(MATCH_URL)
        await expect(page.get_by_role('heading', name='Review past sales names', exact=True)).to_be_visible()
        await expect(page.get_by_text('Old label；Second label', exact=True)).to_be_visible()
        await check_layout(page)
        await page.screenshot(path=OUT/f'matching-list-{width}.png', full_page=True, animations='disabled')
        await prepare_review(page)
        await expect(page.get_by_text('Not recorded', exact=True)).to_be_visible()
        await expect(page.get_by_text('POS 2 · Non-POS 3 · Claw 1 · Display 2 · Employee 1', exact=True)).to_be_visible()
        await choose_target(page)
        save = page.get_by_role('button', name='Save product correction', exact=True)
        await expect(save).to_be_disabled()
        await page.get_by_role('textbox', name='Reason for correction', exact=True).fill('Reviewed both original names against the receipt')
        await expect(save).to_be_disabled()
        await page.get_by_role('checkbox').check()
        await expect(save).to_be_enabled()
        assert state['posts'] == []
        await check_layout(page)
        await page.screenshot(path=OUT/f'matching-review-{width}.png', animations='disabled')
        state['lose_response'] = True
        await save.click()
        await expect(page.get_by_text('Correction result unconfirmed', exact=True)).to_be_visible()
        await expect(page.get_by_role('button', name='Retry', exact=True)).to_be_visible()
        await expect(page.get_by_role('textbox', name='Reason for correction', exact=True)).to_be_disabled()
        await expect(page.get_by_role('checkbox')).to_be_disabled()
        await expect(page.get_by_role('button', name='Cancel', exact=True)).to_be_disabled()
        await expect(page.get_by_label('Operations store', exact=True)).to_be_disabled()
        await page.keyboard.press('Escape')
        await expect(page.get_by_role('dialog')).to_be_visible()
        assert fixture.row() == {**before, 'product_id': fixture.target}
        assert fixture.snapshot(protected) == protected
        await page.get_by_role('button', name='Retry', exact=True).click()
        await expect(page.get_by_role('dialog')).not_to_be_visible()
        await expect(page.get_by_text('Product match corrected and audit saved. Stock and quantities were unchanged.', exact=True)).to_be_visible()
        assert len(state['posts']) == 2 and state['posts'][0] == state['posts'][1]
        assert state['posts'][0]['key'] and state['posts'][0]['body']['product_id'] == fixture.target
        assert fixture.row() == {**before, 'product_id': fixture.target}
        assert fixture.snapshot(protected) == protected
        with closing(fixture.connect()) as con:
            assert con.execute('SELECT COUNT(*) FROM daily_sales_match_audits').fetchone()[0] == 1
            assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert con.execute('PRAGMA foreign_key_check').fetchall() == []
        await prepare_review(page)
        await expect(page.get_by_text('Last corrected', exact=False)).to_be_visible()
        await page.get_by_role('button', name='Cancel', exact=True).click()
    finally:
        if context: await context.close()
        fixture.tearDown(); fixture.doCleanups()


async def matching_guard_checks(browser):
    fixture = SalesHistoryMatchingTests(); fixture.setUp(); context = None
    try:
        with closing(fixture.connect()) as con:
            con.execute("UPDATE products SET search_blob='target correct design' WHERE id=?", (fixture.target,)); con.commit()
        context, page, state = await mount(browser, fixture, 390, ('/api/sales/history-matches', '/api/sales/record/', '/api/products/search'))
        await page.goto(MATCH_URL)
        await prepare_review(page); await choose_target(page)
        await page.get_by_role('textbox', name='Reason for correction', exact=True).fill('Unsubmitted DT draft')
        await page.get_by_label('Operations store', exact=True).select_option('MK')
        await expect(page.get_by_role('dialog')).not_to_be_visible()
        await expect(page.get_by_text('Old label；Second label', exact=True)).to_have_count(0)
        await expect(page.get_by_text('Historical sales access denied', exact=True)).to_be_visible()
        await page.get_by_label('Operations store', exact=True).select_option('ALL')
        await prepare_review(page)
        await expect(page.get_by_text('Choose one store before correcting this row.', exact=True)).to_be_visible()
        await expect(page.get_by_role('textbox', name='Reason for correction', exact=True)).to_be_disabled()
        await expect(page.get_by_role('button', name='Save product correction', exact=True)).to_be_disabled()
        await page.get_by_role('button', name='Cancel', exact=True).click()
        await page.get_by_label('Operations store', exact=True).select_option('DT')
        await prepare_review(page)
        await page.get_by_role('textbox', name='Reason for correction', exact=True).fill('Unsubmitted role draft')
        await page.evaluate("window.__FOUNDATION_AUTH={role:'admin'};window.dispatchEvent(new Event('foundation-auth'))")
        await expect(page.get_by_role('dialog')).not_to_be_visible()
        await prepare_review(page)
        await expect(page.get_by_role('textbox', name='Reason for correction', exact=True)).to_have_value('')
        await choose_target(page)
        await page.get_by_role('textbox', name='Reason for correction', exact=True).fill('Original row was reviewed')
        await page.get_by_role('checkbox').check()
        with closing(fixture.connect()) as con:
            con.execute("UPDATE daily_sales SET notes='Changed after review' WHERE id=?", (fixture.record,)); con.commit()
        await page.get_by_role('button', name='Save product correction', exact=True).click()
        await expect(page.get_by_text('Historical row changed. Refresh before reviewing.', exact=True)).to_be_visible()
        await expect(page.get_by_role('textbox', name='Reason for correction', exact=True)).to_have_value('Original row was reviewed')
        assert state['posts'][-1]['status'] == 409 and fixture.row()['product_id'] == fixture.product_id
        await page.keyboard.press('Escape')
        await expect(page.get_by_role('dialog')).not_to_be_visible()
        with closing(fixture.connect()) as con:
            con.execute("INSERT INTO stock_transactions(product_id,store_id,txn_type,qty,location,date) VALUES (?,?,'report_stock_in',1,'upstairs','2026-09-01')", (fixture.product_id, fixture.store_id)); con.commit()
        await page.get_by_role('button', name='Refresh records', exact=True).click()
        await prepare_review(page)
        await expect(page.get_by_text('This record needs separate reconciliation', exact=True)).to_be_visible()
        await expect(page.get_by_role('button', name='Save product correction', exact=True)).to_be_disabled()
        assert len(state['posts']) == 1
        await check_layout(page)
        await page.screenshot(path=OUT/'matching-blocked-390.png', animations='disabled')
    finally:
        if context: await context.close()
        fixture.tearDown(); fixture.doCleanups()


async def overview_checks(browser, width):
    fixture = StoreOverviewTests(); fixture.setUp(); context = None
    try:
        context, page, state = await mount(browser, fixture, width, ('/api/reports/',))
        await page.goto(OVERVIEW_URL)
        summary = page.get_by_role('region', name='Recorded sales summary')
        await expect(summary).to_be_visible()
        await expect(summary.get_by_text('Known gross subtotal', exact=True)).to_be_visible()
        await expect(summary.get_by_text('$30.00', exact=True)).to_be_visible()
        await expect(summary.get_by_text('1 document', exact=True)).to_be_visible()
        await expect(summary.locator('dd').first).to_have_text('2')
        await expect(page.get_by_text('Original name', exact=True)).to_be_visible()
        await expect(page.get_by_text('2 piece', exact=True)).to_be_visible()
        await expect(page.get_by_text('Garden · New label', exact=True)).to_be_visible()
        await expect(page.get_by_role('link', name='Review past names', exact=True)).to_have_attribute('href', '/sales/matching?from=2026-09-01&to=2026-09-03')
        await expect(page.get_by_role('link', name='Inspect recorded sales', exact=True)).to_have_attribute('href', '/reports?report=sales&from=2026-09-01&to=2026-09-03')
        await page.evaluate('window.scrollTo(0,0)')
        await check_layout(page)
        await page.screenshot(path=OUT/f'overview-{width}.png', animations='disabled')
        await page.get_by_label('Performance source', exact=True).select_option('historical')
        await expect(page.get_by_text('Reported as: Original raw name', exact=True)).to_be_visible()
        await expect(page.get_by_role('cell', name='9', exact=True)).to_be_visible()
        await expect(summary.get_by_text('$30.00', exact=True)).to_be_visible()
        await page.get_by_text('Daily coverage · 3 dates', exact=True).click()
        await expect(page.get_by_role('cell', name='Notes only', exact=True)).to_be_visible()
        await expect(page.get_by_role('cell', name='No report', exact=True)).to_be_visible()
        await check_layout(page)
        await page.get_by_role('cell', name='No report', exact=True).scroll_into_view_if_needed()
        await page.screenshot(path=OUT/f'overview-coverage-{width}.png', animations='disabled')
        state['forced_get'] = (503, 'Overview temporarily unavailable')
        await page.get_by_role('button', name='Refresh insights', exact=True).click()
        await expect(page.get_by_text('Overview temporarily unavailable', exact=True)).to_be_visible()
        await expect(summary).to_have_count(0)
        state['forced_get'] = None
        await page.get_by_role('button', name='Retry insights', exact=True).click()
        await expect(summary).to_be_visible()
        await page.get_by_label('Operations store', exact=True).select_option('MK')
        await expect(page.get_by_text('Report access denied', exact=True)).to_be_visible()
        await expect(summary).to_have_count(0)
        await expect(page.get_by_text('Reported as: Original raw name', exact=True)).to_have_count(0)
        assert state['posts'] == []
    finally:
        if context: await context.close()
        fixture.tearDown(); fixture.doCleanups()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT/'vite.log').open('w') as log:
        process = subprocess.Popen(['node', str(FRONTEND/'node_modules/vite/bin/vite.js'), '--config', str(HERE/'vite.config.mjs'), '--port', '5196'], cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError('Vite test server exited')
                try:
                    _, writer = await asyncio.open_connection('127.0.0.1', 5196); writer.close(); await writer.wait_closed(); break
                except OSError: await asyncio.sleep(.1)
            else: raise RuntimeError('Vite test server did not start')
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    for width in (390, 1440): await matching_retry_checks(browser, width)
                    await matching_guard_checks(browser)
                    for width in (390, 1440): await overview_checks(browser, width)
                finally: await browser.close()
        finally: process.terminate(); process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
    print('PASS historical corrections, immutable retry, scope guards and source-separated insights at 390/1440')
