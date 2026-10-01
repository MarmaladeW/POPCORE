"""Focused receiving navigation checks with isolated API fixtures."""
import asyncio
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, context_with_api
from check_goods_flow import api_payload as goods_payload, select_option

BASE = 'http://127.0.0.1:5187'
OUT = Path(__file__).resolve().parents[3] / '.local' / 'transfer-receiving'


def payload(path, query, request):
    if path == '/api/goods/transfers' and request.method == 'GET':
        if query != 'store_id=1':
            return []
        return [{'id':51,'status':'active','business_date':'2026-09-01',
                 'source_store_name':'Markham','source_location_name':'Warehouse',
                 'destination_location_name':'Downtown Floor',
                 'lines':[{'line_no':1,'product_id':1,'product_name':'Long bilingual product 商品名称','sku':'00123',
                           'native_unit':'piece','outstanding_transit':3,'awaiting_dispatch':0}]}]
    return goods_payload(path, query, request)


async def checks(browser, width, role):
    state = {'mode':'data','api_payload':payload}
    context, page = await context_with_api(browser,state,viewport={'width':width,'height':900},auth={'role':role})
    await context.add_init_script("if (!localStorage.getItem('popcore_selected_store')) localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE+'/goods/transfers')
    await expect(page.get_by_text('Incoming transfers',exact=True)).to_be_visible()
    await expect(page.get_by_text('Markham · Warehouse',exact=True)).to_be_visible()
    await expect(page.get_by_text('2026-09-01',exact=False)).to_be_visible()
    await expect(page.get_by_text('3 pieces in transit',exact=True)).to_be_visible()
    assert all(request['method']=='GET' for request in state['requests'])
    await page.screenshot(path=OUT/f'{role}-{width}.png',full_page=True)
    link = page.get_by_role('link',name='Open transfer #51',exact=True)
    await expect(link).to_have_attribute('href','/goods/transfers?transfer_id=51')
    if role == 'staff':
        await expect(page.get_by_role('button',name='Create transfer',exact=True)).to_have_count(0)
    else:
        await page.get_by_role('button',name='Create transfer',exact=True).click()
        await expect(page.get_by_text('Transfer stock',exact=True)).to_be_visible()
        assert all(request['method']=='GET' for request in state['requests'])
        await page.goto(BASE+'/goods/transfers')
    await page.get_by_role('link',name='Open transfer #51',exact=True).click()
    await expect(page.get_by_text('Transfer #51',exact=True)).to_be_visible()
    assert all(request['method']=='GET' for request in state['requests'])
    await page.goto(BASE+'/goods/transfers')
    state['get_status_for_path']={'/api/goods/transfers':(503,{'error':'Temporary transfer failure'})}
    await page.get_by_role('button',name='Refresh transfers',exact=True).click()
    await expect(page.get_by_text('Temporary transfer failure',exact=True)).to_be_visible()
    await expect(page.get_by_role('link',name='Open transfer #51',exact=True)).to_have_count(0)
    state['get_status_for_path']={}
    await page.get_by_role('button',name='Retry',exact=True).click()
    await expect(page.get_by_role('link',name='Open transfer #51',exact=True)).to_be_visible()
    if role == 'manager':
        state['get_status_for_path']={'/api/goods/transfers':(403,{'error':'Inventory access denied'})}
        await page.evaluate("window.__FOUNDATION_AUTH={role:'staff'}; window.dispatchEvent(new Event('foundation-auth'))")
        await expect(page.get_by_text('Inventory access denied',exact=True).first).to_be_visible()
        await expect(page.get_by_role('link',name='Open transfer #51',exact=True)).to_have_count(0)
        await expect(page.get_by_role('button',name='Create transfer',exact=True)).to_have_count(0)
        state['get_status_for_path']={}
    await page.evaluate("localStorage.setItem('popcore_selected_store',JSON.stringify({id:2,code:'MK',name:'Markham',color:'#6366f1'}))")
    await page.goto(BASE+'/goods/transfers')
    await expect(page.get_by_text('No incoming transfers to receive.',exact=True)).to_be_visible()
    assert any(request['query']=='store_id=2' for request in state['requests'] if request['path']=='/api/goods/transfers')
    assert all(request['method']=='GET' for request in state['requests'])
    await page.evaluate("localStorage.setItem('popcore_selected_store',JSON.stringify({id:0,code:'ALL',name:'All stores',color:'#6366f1'}))")
    await page.goto(BASE+'/goods/transfers')
    await expect(page.get_by_text('Select one store before handling goods.',exact=True)).to_be_visible()
    await context.close()



async def draft_resume_checks(browser):
    state = {'mode':'data','api_payload':payload}
    context, page = await context_with_api(browser,state,auth={'role':'manager'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE+'/goods/receiving')
    await select_option(page,0,'Downtown Floor')
    await page.get_by_label('Scan barcode').fill('00123')
    await page.get_by_label('Scan barcode').press('Enter')
    state['status_for_path']={'/api/goods/receipts':(503,{'error':'Receipt save unconfirmed'})}
    await page.get_by_role('button',name='Save draft for review',exact=True).click()
    await expect(page.get_by_text('Receipt save unconfirmed',exact=True)).to_be_visible()
    await expect(page).to_have_url(BASE+'/goods/receiving')
    state['status_for_path']={}
    state['get_status_for_path']={'/api/goods/receipts/31':(503,{'error':'Receipt refresh unavailable'})}
    await page.get_by_role('button',name='Save draft for review',exact=True).click()
    await expect(page).to_have_url(BASE+'/goods/receiving?receipt_id=31')
    await expect(page.get_by_text('Receipt refresh unavailable',exact=True).first).to_be_visible()
    state['get_status_for_path']={}
    await page.reload()
    await expect(page.get_by_text('Receipt #31',exact=True)).to_be_visible()
    saves = [r for r in state['requests'] if r['method']=='POST' and r['path']=='/api/goods/receipts']
    assert len(saves)==2 and saves[0]['idempotency_key']==saves[1]['idempotency_key']
    assert saves[0]['post_data']==saves[1]['post_data']
    await page.goto(BASE+'/goods/transfers?create=1')
    await select_option(page,0,'MK · Markham Warehouse')
    await select_option(page,1,'DT · Downtown Floor')
    await select_option(page,2,'00123 Long bilingual product 商品名称')
    state['status_for_path']={'/api/goods/transfers':(503,{'error':'Transfer save unconfirmed'})}
    await page.get_by_role('button',name='Create transfer',exact=True).click()
    await expect(page.get_by_text('Transfer save unconfirmed',exact=True)).to_be_visible()
    await expect(page).to_have_url(BASE+'/goods/transfers?create=1')
    state['status_for_path']={}
    await page.get_by_role('button',name='Create transfer',exact=True).click()
    await expect(page).to_have_url(BASE+'/goods/transfers?transfer_id=51')
    await page.reload()
    await expect(page.get_by_text('Transfer #51',exact=True)).to_be_visible()
    saves = [r for r in state['requests'] if r['method']=='POST' and r['path']=='/api/goods/transfers']
    assert len(saves)==2 and saves[0]['idempotency_key']==saves[1]['idempotency_key']
    assert saves[0]['post_data']==saves[1]['post_data']
    await context.close()


async def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'vite.log').open('w') as log:
        process = subprocess.Popen(['node',str(FRONTEND/'node_modules/vite/bin/vite.js'),'--config',str(HERE/'vite.config.mjs'),'--port','5187'],cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Vite test server exited')
                try:
                    _, writer = await asyncio.open_connection('127.0.0.1',5187)
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
                    for width, role in ((390,'staff'),(1440,'manager')):
                        await checks(browser,width,role)
                    await draft_resume_checks(browser)
                finally:
                    await browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
    print('Transfer receiving browser checks passed')
