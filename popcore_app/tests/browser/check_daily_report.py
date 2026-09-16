"""Browser regression for review blocking and literal report metadata (synthetic APIs)."""
import asyncio
import re
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright, expect
from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server
from fixtures import payload as foundation_payload

OUT = Path(__file__).resolve().parents[3] / '.local' / 'daily-report' / 'browser'
PRODUCT = {'id': 1, 'sku': 'TEST-1', 'name_cn_en': 'Test Product', 'jizhanming': 'Test Product', 'price': 999}
ITEM = {'raw_name': 'Test Product', 'qty': 1, 'qty_pos': 1, 'qty_cash': 0,
        'box_size': None, 'section': 'pos', 'flagged': False, 'note': '',
        'unknown_header': None, 'product': PRODUCT, 'score': 100}


def api_payload(path, query, request):
    if path == '/api/sales':
        return []
    if path in ('/api/products/section-aliases', '/api/sales/recorded-dates'):
        return []
    if path == '/api/products/search':
        return [{**PRODUCT, 'id': 2, 'sku': 'TEST-2', 'jizhanming': 'Other Product'}]
    if path == '/api/sales/report-metadata':
        return {'cash_actual': None, 'cash_expected': None, 'cash_difference': None,
                'employee_discounts': [], 'display_sales': [], 'claw_prizes': []}
    if path == '/api/sales/parse_report':
        return {'detected_date': '2026-09-14', 'store': 'DT', 'confirmed': [ITEM],
                'review': [{**ITEM, 'raw_name': 'ambiguous name', 'score': 70,
                            'candidates': [{**PRODUCT, 'score': 70}]}],
                'failed': [], 'unknown_sections': [], 'cash_total_reported': 595,
                'cash_expected_reported': 601.5, 'parser_engine': 'rules', 'multi_day': False,
                'employee_discounts': ['staff购买Test Product*1 卡机18.07'],
                'display_sales': ['Test Product*1 (秘密)'],
                'claw_prizes': ['17:48 Test Product*1'], 'metadata_errors': []}
    if path == '/api/sales/submit_daily_report':
        return {'ok': True}
    return foundation_payload(path, query, False)


async def flow(browser, viewport):
    state = {'api_payload': api_payload}
    context, page = await context_with_api(browser, state, viewport=viewport, auth={'role': 'admin'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store', JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    try:
        await page.goto(BASE + '/sales')
        await expect(page.get_by_role('checkbox', name=re.compile('AI|Anthropic'))).to_have_count(0)
        await expect(page.get_by_text('Anthropic', exact=False)).to_have_count(0)
        await page.locator('textarea').fill('2026.9.14 DT\n卡机汇总：\nTest Product*1\n现金：595/601.5')
        await page.get_by_role('button', name='Parse Report', exact=True).click()
        await expect(page.get_by_label('Physical cash actual', exact=True)).to_have_value('595.00')
        await expect(page.get_by_text(re.compile('AI 解析|规则解析'))).to_have_count(0)
        await expect(page.get_by_label('Physical cash expected', exact=True)).to_have_value('601.50')
        await expect(page.get_by_text('CA$-6.50', exact=False).first).to_be_visible()
        submit = page.get_by_role('button', name='Confirm & Log', exact=False)
        await expect(submit).to_be_disabled()
        assert not any(r['path'] == '/api/products/aliases' for r in state['requests'])
        await page.get_by_label('Physical cash actual', exact=True).scroll_into_view_if_needed()
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        await page.screenshot(path=OUT / f'review-{viewport["width"]}.png')
        await page.get_by_role('tab', name='Review', exact=False).click()
        await page.get_by_role('tabpanel', name='Review', exact=False).get_by_role('combobox').last.fill('Other')
        await page.get_by_text('Other Product (TEST-2)', exact=True).click()
        assert not any(r['path'] == '/api/products/aliases' for r in state['requests'])
        await expect(submit).to_be_enabled()
        await submit.click()
        await expect(page.get_by_text('Report imported', exact=False)).to_be_visible()
        posts = [r for r in state['requests'] if r['path'] == '/api/sales/submit_daily_report']
        assert len(posts) == 1
        body = posts[0]['post_data']
        assert len(body['items']) == 2
        assert body['report_metadata']['cash_actual'] == 595
        assert body['report_metadata']['cash_expected'] == 601.5
        assert len(body['report_metadata']['employee_discounts']) == 1
        assert len(body['report_metadata']['display_sales']) == 1
        assert len(body['report_metadata']['claw_prizes']) == 1
        parses = [r for r in state['requests'] if r['path'] == '/api/sales/parse_report']
        assert parses and all(r['post_data']['engine'] == 'rules' for r in parses)
        assert body['items'][1]['was_top'] is False
        assert body['items'][1]['fuzzy_score'] == 0
        assert not any(r['path'] == '/api/products/aliases' for r in state['requests'])
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
    except Exception:
        await page.screenshot(path=OUT / 'failure.png', full_page=True)
        raise
    finally:
        await context.close()


async def section_choices_flow(browser):
    def payload(path, query, request):
        result = api_payload(path, query, request)
        if path == '/api/sales/parse_report':
            return {**result, 'review': [], 'unknown_sections': ['Register totals', 'Other notes']}
        return result
    state = {'api_payload': payload}
    context, page = await context_with_api(browser, state, auth={'role': 'manager'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store', JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    try:
        await page.goto(BASE + '/sales')
        await page.locator('textarea').fill('2026.9.14 DT\nRegister totals:\nTest Product*1\nOther notes:')
        await page.get_by_role('button', name='Parse Report', exact=True).click()
        for header, label in [('Register totals', '卡机 POS'), ('Other notes', '跳过/忽略')]:
            await page.get_by_role('alert').filter(has_text='Unknown section: ' + header).get_by_role('combobox').click()
            await page.get_by_title(label, exact=True).click()
        assert not any(r['method'] == 'POST' and 'section-aliases' in r['path'] for r in state['requests'])
        await page.get_by_role('button', name='Confirm & Log', exact=False).click()
        await expect(page.get_by_text('Report imported', exact=False)).to_be_visible()
        body = next(r['post_data'] for r in state['requests'] if r['path'] == '/api/sales/submit_daily_report')
        assert body['section_choices'] == [
            {'header': 'Register totals', 'section': 'pos'}, {'header': 'Other notes', 'section': 'skip'}]
    finally:
        await context.close()


async def metadata_only_flow(browser):
    def payload(path, query, request):
        if path == '/api/sales/report-metadata':
            return {'cash_actual': 0, 'cash_expected': 0, 'cash_difference': 0,
                    'employee_discounts': [], 'display_sales': [],
                    'claw_prizes': ['17:48 Test Product*1']}
        return api_payload(path, query, request)
    context, page = await context_with_api(browser, {'api_payload': payload}, auth={'role': 'admin'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store', JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    try:
        await page.goto(BASE + '/sales')
        await expect(page.get_by_text('17:48 Test Product*1', exact=True)).to_be_visible()
        await expect(page.get_by_role('button', name='Clear Day', exact=False)).to_be_visible()
        await expect(page.get_by_role('button', name='Parse Report', exact=True)).to_have_count(0)
    finally:
        await context.close()


NEW_NOTES = {
    'cash_exchanges': ('EMT 换现金 / Cash exchanges', 'EMT换现金*2 (17:48)'),
    'claw_stock_in': ('娃娃机入库 / Claw stock in', '入娃娃机：Test Product*2'),
    'display_stock_in': ('已拆 Display 入库 / Opened display stock in', '入店display：Test Product*1 (已拆)'),
    'display_stock_out': ('已拆 Display 出库 / Opened display stock out', '出店display：Test Product*1 (已拆)'),
}


async def annotation_flow(browser, key):
    label, note = NEW_NOTES[key]
    saved = None

    def payload(path, query, request):
        nonlocal saved
        if path == '/api/sales/report-metadata' and saved is not None:
            return saved
        if path == '/api/sales/parse_report':
            return {**api_payload(path, query, request), 'confirmed': [], 'review': [],
                    'cash_total_reported': None, 'cash_expected_reported': None,
                    'employee_discounts': [], 'display_sales': [], 'claw_prizes': [], key: [note]}
        if path == '/api/sales/submit_daily_report':
            body = request.post_data_json
            assert body['items'] == []
            saved = body['report_metadata']
            assert saved[key] == [note]
            assert saved['cash_actual'] is None and saved['cash_expected'] is None
            return {'ok': True}
        return api_payload(path, query, request)

    state = {'api_payload': payload}
    context, page = await context_with_api(browser, state, auth={'role': 'admin'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store', JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    try:
        await page.goto(BASE + '/sales')
        await page.locator('textarea').fill('2026.9.14 DT\n' + note)
        await page.get_by_role('button', name='Parse Report', exact=True).click()
        await expect(page.get_by_text(label, exact=True)).to_be_visible()
        await expect(page.get_by_text(note, exact=True)).to_be_visible()
        await page.get_by_role('button', name='Confirm & Log', exact=False).click()
        await page.get_by_role('button', name='View Sales', exact=True).click()
        await expect(page.get_by_text(label, exact=True)).to_be_visible()
        await expect(page.get_by_text(note, exact=True)).to_be_visible()
        await expect(page.get_by_role('button', name='Clear Day', exact=False)).to_be_visible()
        await expect(page.get_by_role('button', name='Re-import', exact=False)).to_be_visible()
        await expect(page.get_by_role('button', name='Parse Report', exact=True)).to_have_count(0)
        assert not any(r['path'] == '/api/products/aliases' for r in state['requests'])
    finally:
        await context.close()


async def receipt_flow(browser, viewport):
    def payload(path, query, request):
        if path == '/api/sales/parse_report':
            return {**api_payload(path, query, request), 'confirmed': [],
                    'review': [{**ITEM, 'section': 'stock_in', 'qty': 2, 'box_size': 6,
                                'loose_qty': 2, 'candidates': [{**PRODUCT, 'score': 100}]}]}
        return api_payload(path, query, request)

    state = {'api_payload': payload}
    context, page = await context_with_api(browser, state, viewport=viewport, auth={'role': 'admin'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store', JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    try:
        await page.goto(BASE + '/sales')
        await page.locator('textarea').fill('2026.9.14 DT\n入店：Test Product 6*2+2')
        await page.get_by_role('button', name='Parse Report', exact=True).click()
        await page.get_by_role('tab', name='Review', exact=False).click()
        await expect(page.get_by_text('合计 14 件', exact=True)).to_be_visible()
        await page.get_by_label('Test Product 包数', exact=True).fill('3')
        await page.get_by_label('Test Product 包装规格', exact=True).fill('4')
        loose = page.get_by_label('Test Product 散件', exact=True)
        await loose.fill('1')
        await expect(page.get_by_text('合计 13 件', exact=True)).to_be_visible()
        await page.get_by_role('button', name=re.compile(r'接\s*受')).click()
        submit = page.get_by_role('button', name='Confirm & Log', exact=False)
        for invalid in ('-1', '0.5'):
            await loose.fill(invalid)
            await loose.press('Tab')
            await expect(submit).to_be_disabled()
        await loose.fill('1')
        packs = page.get_by_label('Test Product 包数', exact=True)
        await packs.fill('0')
        await packs.press('Tab')
        await expect(submit).to_be_disabled()
        await packs.fill('3')
        await expect(submit).to_be_enabled()
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        await page.screenshot(path=OUT / f'receipt-{viewport["width"]}.png', full_page=True)
        await submit.click()
        await expect(page.get_by_text('Report imported', exact=False)).to_be_visible()
        body = next(r['post_data'] for r in state['requests'] if r['path'] == '/api/sales/submit_daily_report')
        assert len(body['items']) == 1
        item = body['items'][0]
        assert (item['num_boxes'], item['box_size'], item['loose_qty']) == (3, 4, 1)
        assert item['source_bucket'] == 'review' and item['was_top'] is True
        assert not any(r['path'] == '/api/products/aliases' for r in state['requests'])
    except Exception:
        await page.screenshot(path=OUT / 'receipt-failure.png', full_page=True)
        raise
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
                    await section_choices_flow(browser)
                    await metadata_only_flow(browser)
                    for key in NEW_NOTES:
                        await annotation_flow(browser, key)
                    for size in ({'width': 1440, 'height': 1000}, {'width': 390, 'height': 844}):
                        await flow(browser, size)
                        await receipt_flow(browser, size)
                finally:
                    await browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
    print('Daily report browser checks passed')


if __name__ == '__main__':
    asyncio.run(main())
