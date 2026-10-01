"""Series worksheets preserve exact lines, blank counts and unconfirmed drafts."""
import asyncio
import json
import re
import subprocess
import sys
from contextlib import closing
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT, context_with_api
from check_design_inventory import payload as series_payload

BASE = 'http://127.0.0.1:5189'
OUT = ROOT / '.local' / 'series-worksheet'


def payload(path, query, request):
    data = series_payload(path, query, request)
    if path == '/api/inventory/series':
        products = data['series'][0]['products']
        example = next(product for product in products if product['id'] == 14)
        products.extend({**example, 'id':100+n, 'name':f'Design {n}', 'design_name':f'Design {n}'} for n in range(3,13))
        next(product for product in products if product['id'] == 112)['stock_unit'] = 'box'
        data['locations'].append({'id':2,'store_id':1,'store_code':'DT','code':'back','name':'Back room','opening_verified':False})
    if path == '/api/goods/counts':
        return {'id':91,'version':1,'status':'draft'}
    if path == '/api/goods/receipts':
        return {'id':92,'version':1,'status':'draft'}
    if path == '/api/goods/counts/91':
        return {'id':91,'version':1,'status':'draft','store_id':1,'location_id':1,'disposition':'saleable','business_date':'2026-09-30','lines':[
            {'line_no':1,'product_id':13,'native_unit':'piece','expected_quantity':3,'observed_quantity':0,'captured_balance_version':2},
            {'line_no':2,'product_id':14,'native_unit':'piece','expected_quantity':0,'observed_quantity':2,'captured_balance_version':0}]}
    if path == '/api/goods/receipts/92':
        return {'id':92,'version':1,'status':'draft','store_id':1,'destination_location_id':1,'business_date':'2026-09-30','shipment_reference':None,'supplier':None,'lines':[
            {'line_no':1,'product_id':14,'native_unit':'piece','expected_quantity':None,'saleable_quantity':2,'damaged_quantity':0,'hold_quantity':0,'discrepancy_note':None}]}
    if path == '/api/products/13':
        return {'id':13,'sku':'13','jizhanming':'Moon','design_name':'Moon','stock_form':'confirmed_design','stock_unit':'piece','identity_status':'verified'}
    return data


async def screenshot(page, drawer, name):
    await expect(drawer).to_be_in_viewport(ratio=.99)
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert await drawer.evaluate('element => element.scrollWidth <= element.clientWidth')
    await page.screenshot(path=OUT/name, animations='disabled')


async def checks(browser, width):
    state = {'mode':'data','api_payload':payload}
    context, page = await context_with_api(browser,state,viewport={'width':width,'height':900},auth={'role':'staff'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE+'/stock?series_id=7')
    await page.get_by_role('button',name='Count series',exact=True).click(timeout=5000)
    drawer = page.get_by_role('dialog',name='Count series',exact=True)
    await expect(drawer.get_by_role('spinbutton')).to_have_count(12)
    await expect(drawer.get_by_label('Count Moon',exact=True)).to_have_value('')
    await expect(drawer.get_by_role('button',name='Review count',exact=True)).to_be_disabled()
    await expect(drawer.get_by_label('Location',exact=True).locator('option[value="2"]')).to_have_attribute('disabled', '')
    await drawer.get_by_label('Location',exact=True).select_option('1')
    await expect(drawer.get_by_label('Count Design 12',exact=True)).to_be_disabled()
    await drawer.get_by_label('Count Moon',exact=True).fill('0')
    await drawer.get_by_label('Count Sun',exact=True).fill('2')
    await expect(drawer.get_by_text('2 of 12 designs entered',exact=True)).to_be_visible()
    await screenshot(page,drawer,f'count-{width}.png')
    attempts = []
    async def lose_first_response(route):
        attempts.append({'body':route.request.post_data_json,'key':route.request.headers.get('idempotency-key')})
        if len(attempts) == 1:
            await route.abort('failed')
        else:
            await route.fulfill(status=200,content_type='application/json',body=json.dumps({'id':91,'version':1,'status':'draft'}))
    await page.route('**/api/goods/counts',lose_first_response)
    await drawer.get_by_role('button',name='Review count',exact=True).click()
    await expect(drawer.get_by_role('button',name='Retry',exact=True)).to_be_visible()
    await expect(drawer.get_by_label('Count Moon',exact=True)).to_be_disabled()
    await expect(page.get_by_label('Operations store',exact=True)).to_be_disabled()
    await page.keyboard.press('Escape')
    await expect(drawer).to_be_visible()
    await drawer.get_by_role('button',name='Retry',exact=True).click()
    await expect(page).to_have_url(re.compile(r'/goods/counts\?count_id=91$'))
    await expect(page.get_by_text('Count #91',exact=True)).to_be_visible()
    assert len(attempts) == 2 and attempts[0] == attempts[1] and attempts[0]['key']
    assert attempts[0]['body']['location_id'] == 1
    assert attempts[0]['body']['lines'] == [
        {'product_id':13,'unit':'piece','observed_quantity':0},
        {'product_id':14,'unit':'piece','observed_quantity':2}]

    await page.goto(BASE+'/stock?series_id=7')
    await page.get_by_role('button',name='Receive series',exact=True).click()
    drawer = page.get_by_role('dialog',name='Receive series',exact=True)
    await drawer.get_by_label('Location',exact=True).select_option('1')
    await drawer.get_by_label('Receive Sun',exact=True).fill('0')
    await expect(drawer.get_by_role('button',name='Review receipt',exact=True)).to_be_disabled()
    await drawer.get_by_label('Receive Sun',exact=True).fill('2')
    await expect(drawer.get_by_label('Receive Moon',exact=True)).to_have_value('')
    await screenshot(page,drawer,f'receive-{width}.png')
    await drawer.get_by_role('button',name='Review receipt',exact=True).click()
    await expect(page).to_have_url(re.compile(r'/goods/receiving\?receipt_id=92$'))
    await expect(page.get_by_text('Receipt #92',exact=True)).to_be_visible()
    saves = [r for r in state['requests'] if r['path']=='/api/goods/receipts' and r['method']=='POST']
    assert len(saves) == 1 and saves[0]['idempotency_key']
    assert saves[0]['post_data']['destination_location_id'] == 1 and saves[0]['post_data']['store_id'] == 1
    assert saves[0]['post_data']['lines'] == [{'product_id':14,'unit':'piece','expected_quantity':None,'saleable_quantity':2,'damaged_quantity':0,'hold_quantity':0}]
    assert not [r for r in state['requests'] if r['method']=='POST' and (r['path'].endswith('/post') or r['path'].endswith('/submit'))]

    await page.goto(BASE+'/stock?series_id=7')
    await page.get_by_role('button',name='Count series',exact=True).click()
    await page.get_by_role('dialog').get_by_label('Location',exact=True).select_option('1')
    await page.get_by_role('dialog').get_by_label('Count Moon',exact=True).fill('9')
    await page.evaluate("() => {window.__FOUNDATION_AUTH={role:'manager'};window.dispatchEvent(new Event('foundation-auth'))}")
    await expect(page.get_by_role('dialog')).not_to_be_visible()
    await page.get_by_role('button',name='Count series',exact=True).click()
    await expect(page.get_by_role('dialog').get_by_label('Count Moon',exact=True)).to_have_value('')
    await page.keyboard.press('Escape')
    await expect(page.get_by_role('dialog')).not_to_be_visible()
    await context.close()



async def real_flask_checks(browser):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_design_identification import DesignIdentificationTests
    fixture = DesignIdentificationTests(); fixture.setUp()
    with closing(fixture.connect()) as con:
        con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)", (fixture.store_id,))
        con.commit()
    context = await browser.new_context(viewport={'width':1440,'height':900})
    await context.add_init_script("window.__FOUNDATION_AUTH={role:'staff',token:'staff'};localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown'}))")
    page = await context.new_page()
    attempts = []
    def post(path, body, key, role='staff'):
        response = fixture.client.post(path,json=body,headers={**fixture.headers(role),'Idempotency-Key':key})
        assert response.status_code == 200, response.get_json()
        return response.get_json()
    async def flask_boundary(route):
        parsed = urlsplit(route.request.url)
        if parsed.hostname not in ('127.0.0.1','localhost'):
            await route.abort(); return
        if not parsed.path.startswith('/api/'):
            await route.continue_(); return
        headers = fixture.headers('staff')
        key = route.request.headers.get('idempotency-key')
        if key: headers['Idempotency-Key'] = key
        body = route.request.post_data_json if route.request.post_data else None
        response = fixture.client.open(parsed.path+('?' + parsed.query if parsed.query else ''),method=route.request.method,headers=headers,json=body)
        if parsed.path == '/api/goods/counts' and route.request.method == 'POST':
            assert response.status_code == 201, response.get_json()
            attempts.append((body,key,response.get_json()))
            if len(attempts) == 1:
                await route.abort('failed'); return
        await route.fulfill(status=response.status_code,body=response.data,content_type=response.content_type)
    await page.route('**/*',flask_boundary)
    try:
        before = fixture.snapshot(('inventory_balances','inventory_documents','inventory_movements'))
        await page.goto(BASE+f'/stock?series_id={fixture.series_id}')
        await page.get_by_role('button',name='Count series',exact=True).click()
        drawer = page.get_by_role('dialog',name='Count series',exact=True)
        await drawer.get_by_label('Location',exact=True).select_option(str(fixture.floor))
        await drawer.get_by_label('Count A',exact=True).fill('0')
        await drawer.get_by_label('Count B',exact=True).fill('3')
        await drawer.get_by_role('button',name='Review count',exact=True).click()
        await expect(drawer.get_by_role('button',name='Retry',exact=True)).to_be_visible()
        assert fixture.snapshot(('inventory_balances','inventory_documents','inventory_movements')) == before
        await drawer.get_by_role('button',name='Retry',exact=True).click()
        await expect(page).to_have_url(re.compile(r'/goods/counts\?count_id=\d+$'))
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        assert attempts[0][0]['lines'] == [{'product_id':fixture.design_ids[0],'unit':'piece','observed_quantity':0},{'product_id':fixture.design_ids[1],'unit':'piece','observed_quantity':3}]
        count = attempts[0][2]
        submitted = post(f"/api/goods/counts/{count['id']}/submit",{'expected_version':count['version']},'worksheet-submit')
        assert fixture.snapshot(('inventory_balances','inventory_documents','inventory_movements')) == before
        post(f"/api/goods/counts/{count['id']}/approve",{'expected_version':submitted['version'],'reason':'Reviewed full-series observations'},'worksheet-approve','manager')
        quantities = fixture.quantities()
        assert quantities.get(fixture.design_ids[0],0) == 0 and quantities[fixture.design_ids[1]] == 3 and quantities[fixture.product_id] == 5
        before = fixture.snapshot(('inventory_balances','inventory_documents','inventory_movements'))
        await page.goto(BASE+f'/stock?series_id={fixture.series_id}')
        await page.get_by_role('button',name='Receive series',exact=True).click()
        drawer = page.get_by_role('dialog',name='Receive series',exact=True)
        await drawer.get_by_label('Location',exact=True).select_option(str(fixture.floor))
        await drawer.get_by_label('Receive A',exact=True).fill('2')
        await drawer.get_by_label('Receive B',exact=True).fill('1')
        await drawer.get_by_role('button',name='Review receipt',exact=True).click()
        await expect(page).to_have_url(re.compile(r'/goods/receiving\?receipt_id=\d+$'))
        assert fixture.snapshot(('inventory_balances','inventory_documents','inventory_movements')) == before
        receipt_id = int(urlsplit(page.url).query.split('=')[1])
        receipt = fixture.client.get(f'/api/goods/receipts/{receipt_id}',headers=fixture.headers()).get_json()
        assert [(line['product_id'],line['saleable_quantity']) for line in receipt['lines']] == [(fixture.design_ids[0],2),(fixture.design_ids[1],1)]
        post(f'/api/goods/receipts/{receipt_id}/post',{'expected_version':receipt['version']},'worksheet-post')
        quantities = fixture.quantities()
        assert quantities[fixture.design_ids[0]] == 2 and quantities[fixture.design_ids[1]] == 4 and quantities[fixture.product_id] == 5
    finally:
        await context.close()
        fixture.tearDown(); fixture.doCleanups()


async def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'vite.log').open('w') as log:
        process = subprocess.Popen(['node',str(FRONTEND/'node_modules/vite/bin/vite.js'),'--config',str(HERE/'vite.config.mjs'),'--port','5189'],cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError('Vite stopped')
                try:
                    _, writer = await asyncio.open_connection('127.0.0.1',5189); writer.close(); await writer.wait_closed(); break
                except OSError: await asyncio.sleep(.1)
            else: raise RuntimeError('Vite did not start')
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    for width in (390,1440): await checks(browser,width)
                    await real_flask_checks(browser)
                finally: await browser.close()
        finally:
            process.terminate(); process.wait(timeout=10)

if __name__ == '__main__':
    asyncio.run(main())
    print('PASS: series worksheets exact lines, blank/zero, identical retry, scope reset, responsive drawers and real Flask draft/post stock')
