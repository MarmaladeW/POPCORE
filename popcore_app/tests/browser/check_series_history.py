"""Read-only series history filtering, pagination, and authorized document inspection."""
import asyncio
import subprocess
from urllib.parse import parse_qs
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT, context_with_api
from check_design_inventory import payload as inventory_payload

BASE = 'http://127.0.0.1:5191'
OUT = ROOT / '.local' / 'series-history'


def payload(path, query, request):
    if path == '/api/inventory/series/7/history':
        params = parse_qs(query)
        item = {'id':90,'document_id':80,'kind':'consume','product_id':12,'product_name':'Unidentified box','store_code':'DT','location_id':1,'location_name':'Floor','disposition':'saleable','quantity':-2,'business_date':'2026-09-30','posted_at':'2026-09-30T12:00:00Z','reason':'Visually confirmed Sun','open_set_id':61,'actor_sub':'auth0|staff-recorded','source_type':'design_identification','source_id':'batch-1'}
        if params.get('product_id') == ['14']:
            return {'items':[{**item,'id':92,'document_id':81,'product_id':14,'product_name':'Sun','quantity':2,'kind':'receipt','open_set_id':None}], 'has_more':False,'next_before_id':None}
        if 'before_id' in params:
            assert params['before_id'] == ['90'], params
            return {'items':[{**item,'id':89,'document_id':79,'reason':'Earlier delivery','kind':'receipt','quantity':4,'open_set_id':None}],'has_more':False,'next_before_id':None}
        return {'items':[item],'has_more':True,'next_before_id':90}
    if path.startswith('/api/inventory/documents/'):
        doc_id = int(path.rsplit('/',1)[1])
        return {'id':doc_id,'kind':'consume','actor_sub':'auth0|staff-recorded','business_date':'2026-09-30','posted_at':'2026-09-30T12:00:00Z','reason':'Visually confirmed Sun','source_type':'design_identification','source_id':'batch-1','correction_of':80 if doc_id==84 else None,'corrections':[84] if doc_id==80 else [],'lines':[{'line_no':1,'product_id':12,'native_unit':'box','quantity':2,'from_location_id':1,'from_disposition':'saleable','to_location_id':None,'to_disposition':None,'from_version':2,'to_version':None,'conversion_id':None,'conversion_factor':None,'open_set_id':61}]}
    return inventory_payload(path,query,request)


async def check(browser, width):
    state = {'mode':'data','api_payload':payload}
    context,page = await context_with_api(browser,state,viewport={'width':width,'height':900},auth={'role':'staff'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE+'/stock?series_id=7')
    await page.get_by_role('button',name='Show movement history',exact=True).click()
    drawer = page.get_by_role('dialog',name='Movement history',exact=True)
    await expect(drawer.get_by_text('Recorded by auth0|staff-recorded',exact=True)).to_be_visible()
    await drawer.get_by_role('button',name='Load older',exact=True).click()
    await expect(drawer.get_by_text('Earlier delivery',exact=True)).to_be_visible()
    await expect(drawer.get_by_role('button',name='Load older',exact=True)).to_have_count(0)
    await expect(drawer.get_by_role('button',name='Document #80',exact=True)).to_have_count(1)
    state['get_status_for_path']={'/api/inventory/documents/80':(403,{'error':'Inventory access denied'})}
    await drawer.get_by_role('button',name='Document #80',exact=True).click()
    await expect(drawer.get_by_text('Inventory access denied',exact=True)).to_be_visible()
    await expect(drawer.get_by_role('heading',name='Document lines',exact=True)).to_have_count(0)
    state['get_status_for_path']={}
    await drawer.get_by_role('button',name='Retry document',exact=True).click()
    await expect(drawer.get_by_text('From DT · Floor · saleable',exact=True)).to_be_visible()
    await expect(drawer.get_by_text('design identification · batch-1',exact=True)).to_be_visible()
    await expect(drawer.get_by_text('Opened set #61',exact=True).last).to_be_visible()
    await drawer.get_by_role('button',name='Document #84',exact=True).click()
    await expect(drawer.get_by_role('heading',name='Document #84',exact=True)).to_be_visible()
    await expect(drawer.get_by_text('Corrects',exact=False)).to_be_visible()
    await drawer.get_by_role('button',name='Back to movements',exact=True).click()
    await expect(drawer.get_by_text('Earlier delivery',exact=True)).to_be_visible()
    await drawer.get_by_label('From date',exact=True).fill('2026-09-30')
    await drawer.get_by_label('Through date',exact=True).fill('2026-09-01')
    await expect(drawer.get_by_role('button',name='Apply filters',exact=True)).to_be_disabled()
    await drawer.get_by_label('Through date',exact=True).fill('2026-09-30')
    await drawer.get_by_label('Product',exact=True).select_option('14')
    await drawer.get_by_role('button',name='Apply filters',exact=True).click()
    await expect(drawer.get_by_text('Sun',exact=True)).to_be_visible()
    await expect(drawer.get_by_text('Earlier delivery',exact=True)).to_have_count(0)
    requests = [r for r in state['requests'] if r['path']=='/api/inventory/series/7/history']
    query = parse_qs(requests[-1]['query'])
    assert query.get('product_id')==['14'] and query.get('date_from')==['2026-09-30'] and query.get('date_to')==['2026-09-30'],query
    assert 'before_id' not in query,query
    await drawer.get_by_role('button',name='Apply filters',exact=True).click()
    await expect(drawer.get_by_role('button',name='Document #81',exact=True)).to_have_count(1)
    await expect(drawer.get_by_role('button',name='Load older',exact=True)).to_have_count(0)
    assert await drawer.evaluate('element => element.scrollWidth <= element.clientWidth')
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await page.screenshot(animations='disabled',path=OUT/f'history-{width}.png')
    await page.keyboard.press('Escape')
    await expect(drawer).not_to_be_visible()
    state['get_status_for_path']={'/api/inventory/series/7/history':(503,{'error':'History unavailable'})}
    await page.get_by_role('button',name='Show movement history',exact=True).click()
    await expect(drawer.get_by_text('History unavailable',exact=True)).to_be_visible()
    await expect(drawer.get_by_role('button',name='Document #81',exact=True)).to_have_count(0)
    state['get_status_for_path']={}
    await drawer.get_by_role('button',name='Retry history',exact=True).click()
    await expect(drawer.get_by_role('button',name='Document #80',exact=True)).to_be_visible()
    await page.get_by_label('Operations store',exact=True).select_option('MK',force=True)
    await expect(drawer).not_to_be_visible()
    assert all(r['method']=='GET' for r in state['requests'])
    await context.close()


async def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'vite.log').open('w') as log:
        process=subprocess.Popen(['node',str(FRONTEND/'node_modules/vite/bin/vite.js'),'--config',str(HERE/'vite.config.mjs'),'--port','5191'],cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError('Vite test server exited')
                try:
                    _,writer=await asyncio.open_connection('127.0.0.1',5191);writer.close();await writer.wait_closed();break
                except OSError: await asyncio.sleep(.1)
            else: raise RuntimeError('Vite test server did not start')
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                try:
                    for width in (390,1440): await check(browser,width)
                finally: await browser.close()
        finally:
            process.terminate();process.wait(timeout=10)

if __name__=='__main__':
    asyncio.run(main())
    print('Series history browser checks passed')
