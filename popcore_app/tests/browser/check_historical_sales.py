"""Historical sales scope, saved-price truth and mapping UX against isolated APIs."""
import asyncio
import json
import subprocess
from urllib.parse import parse_qs
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT, context_with_api
from fixtures import payload as foundation_payload

BASE = 'http://127.0.0.1:5195'
OUT = ROOT / '.local' / 'historical-sales'


def api(path, query, request):
    params = parse_qs(query)
    store = params.get('store_code', ['DT'])[0]
    date = params.get('date', ['2026-09-18'])[0]
    if path == '/api/sales':
        return [{'id':1, 'product_id':1, 'date':date, 'qty_pos':2, 'qty_cash':0, 'qty_sold':2, 'notes':'', 'sku':f'{store}-RED', 'jizhanming':f'{store} Red design', 'name_cn_en':'', 'price':999, 'unit_price':10, 'ip_series':'Garden'},
                {'id':2, 'product_id':2, 'date':date, 'qty_pos':1, 'qty_cash':0, 'qty_sold':1, 'notes':'', 'sku':f'{store}-BLUE', 'jizhanming':f'{store} Blue design', 'name_cn_en':'', 'price':999, 'unit_price':None, 'ip_series':'Garden'}]
    if path == '/api/sales/summary': return [{'date':'2026-09-18','product_count':2,'total_pos':3,'total_cash':0,'total_sold':3},{'date':'2026-09-01','product_count':1,'total_pos':1,'total_cash':0,'total_sold':1}]
    if path == '/api/products/aliases': return []
    if path == '/api/products/search': return [{'id':3,'sku':'GARDEN-GREEN','jizhanming':'Generic garden','series_name':'Garden','design_name':'Green','stock_form':'confirmed_design'}]
    if request.method != 'GET': return {'ok':True}
    return foundation_payload(path,query)


async def setup(browser,width=1440):
    state={'api_payload':api}
    context,page=await context_with_api(browser,state,viewport={'width':width,'height':1000},auth={'role':'manager'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown'}))")
    return context,page,state


async def detail_checks(browser,width):
    context,page,state=await setup(browser,width)
    await page.goto(BASE+'/sales/day/2026-09-18')
    await expect(page.get_by_text('DT Red design',exact=True).first).to_be_visible()
    await page.get_by_label('Operations store',exact=True).select_option('MK')
    await expect(page.get_by_text('MK Red design',exact=True)).to_be_visible()
    await expect(page.get_by_text('DT Red design',exact=True)).to_have_count(0)
    await expect(page.get_by_text('Saved-price estimate',exact=True).first).to_be_visible()
    await expect(page.get_by_text('1 product has no saved price.',exact=False)).to_be_visible()
    await expect(page.get_by_role('link',name='Historical reports',exact=True)).to_have_attribute('href','/sales?date=2026-09-18')
    state['get_status_for_path']={'/api/sales':(503,{'error':'History unavailable'})}
    await page.get_by_label('Operations store',exact=True).select_option('DT')
    await expect(page.get_by_role('alert').filter(has_text='History unavailable')).to_be_visible()
    await expect(page.get_by_text('MK Red design',exact=True)).to_have_count(0)
    state['get_status_for_path']={}
    await page.get_by_role('button',name='Retry',exact=True).click()
    await expect(page.get_by_text('DT Red design',exact=True).first).to_be_visible()
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await page.screenshot(path=OUT/f'detail-{width}.png',full_page=True)
    await context.close()


async def sales_checks(browser,width):
    context,page,state=await setup(browser,width)
    await page.goto(BASE+'/sales?date=2026-09-18')
    await expect(page.get_by_role('heading',name='Historical reports',exact=True)).to_be_visible()
    await expect(page.get_by_text('DT Red design',exact=True).first).to_be_visible()
    assert any('date=2026-09-18' in req['query'] for req in state['requests'] if req['path']=='/api/sales')
    await expect(page.get_by_text('Saved-price estimate',exact=True).first).to_be_visible()
    await expect(page.get_by_text('CA$20.00',exact=True).first).to_be_visible()
    await expect(page.get_by_text('1 product has no saved price.',exact=False)).to_be_visible()
    await expect(page.get_by_text('Latest 7 recorded dates',exact=True)).to_be_visible()
    await expect(page.get_by_role('link',name='Review past names',exact=True)).to_have_attribute('href','/sales/matching')
    await expect(page.get_by_role('link',name='Insights & reports',exact=True)).to_have_attribute('href','/reports')
    qty=page.get_by_label('POS quantity for DT Red design',exact=True)
    await qty.fill('7')
    await page.get_by_role('heading',name='Historical reports',exact=True).click()
    assert not [req for req in state['requests'] if req['method']=='POST']
    await expect(page.get_by_role('button',name='Save DT Red design',exact=True)).to_be_visible()
    state['status_for_path']={'/api/sales/upsert':(409,{'error':'This report requires reconciliation'})}
    await page.get_by_role('button',name='Save DT Red design',exact=True).click()
    await expect(page.get_by_role('alert').filter(has_text='This report requires reconciliation')).to_be_visible()
    await expect(qty).to_have_value('7')
    await page.get_by_role('button',name='Cancel changes to DT Red design',exact=True).click()
    await expect(qty).to_have_value('2')
    state['status_for_path']={}
    await qty.fill('5')
    blue=page.get_by_label('POS quantity for DT Blue design',exact=True)
    await blue.fill('4')
    await page.get_by_role('button',name='Save DT Red design',exact=True).click()
    await expect(page.get_by_role('button',name='Save DT Red design',exact=True)).to_have_count(0)
    await expect(qty).to_have_value('5')
    await expect(blue).to_have_value('4')
    saved=[req for req in state['requests'] if req['path']=='/api/sales/upsert'][-1]['post_data']
    assert saved=={'product_id':1,'date':'2026-09-18','qty_pos':5,'qty_cash':0,'notes':'','store_code':'DT'}
    await page.get_by_role('button',name='Cancel changes to DT Blue design',exact=True).click()
    await qty.fill('8')
    await page.get_by_label('Operations store',exact=True).select_option('MK')
    await expect(page.get_by_label('POS quantity for MK Red design',exact=True)).to_have_value('2')
    assert len([req for req in state['requests'] if req['method']=='POST'])==2
    await page.get_by_role('combobox',name='Add historical product',exact=True).fill('garden')
    await page.get_by_text('Garden · Green · Confirmed',exact=False).click()
    await page.get_by_label('Operations store',exact=True).select_option('DT')
    await expect(page.get_by_role('combobox',name='Add historical product',exact=True)).to_have_value('')
    await page.get_by_role('button',name='Manage name mappings',exact=True).click()
    await expect(page.get_by_text('Saved name mappings',exact=True)).to_be_visible()
    await page.get_by_label('Staff name',exact=True).fill('green garden')
    await page.get_by_role('combobox',name='Mapped product',exact=True).fill('garden')
    await page.get_by_text('Garden · Green · Confirmed',exact=False).click()
    await page.get_by_role('button',name='Save name mapping',exact=True).click()
    await expect(page.get_by_label('Staff name',exact=True)).to_have_value('')
    writes=[req for req in state['requests'] if req['path']=='/api/products/aliases' and req['method']=='POST']
    assert len(writes)==1 and writes[0]['post_data']=={'product_id':3,'alias':'green garden'}
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await page.get_by_role('heading',name='Historical reports',exact=True).click()
    await page.screenshot(path=OUT/f'historical-{width}.png')
    await context.close()


async def pending_and_race_checks(browser):
    context,page,state=await setup(browser)
    await page.goto(BASE+'/sales?date=2026-09-18')
    await expect(page.get_by_label('POS quantity for DT Red design',exact=True)).to_be_visible()
    await page.get_by_label('POS quantity for DT Red design',exact=True).fill('6')
    await page.evaluate("window.__FOUNDATION_AUTH={role:'admin'};window.dispatchEvent(new Event('foundation-auth'))")
    await expect(page.get_by_label('POS quantity for DT Red design',exact=True)).to_have_value('2')
    await page.get_by_role('button',name='Manage name mappings',exact=True).click()
    search_release=asyncio.Event()
    first_search=asyncio.Event()
    async def search(route):
        query=parse_qs(route.request.url.split('?',1)[1]).get('q',[''])[0]
        if query=='old':first_search.set();await search_release.wait()
        product={'id':7 if query=='old' else 8,'sku':query.upper(),'jizhanming':query+' product'}
        await route.fulfill(content_type='application/json',body=json.dumps([product]))
    await page.route('**/api/products/search?**',search)
    combo=page.get_by_role('combobox',name='Mapped product',exact=True)
    await combo.fill('old');await first_search.wait();await combo.fill('new')
    await expect(page.get_by_text('new product (NEW)',exact=True)).to_be_visible()
    search_release.set()
    await page.wait_for_timeout(150)
    await expect(page.get_by_text('old product (OLD)',exact=True)).to_have_count(0)
    await page.get_by_text('new product (NEW)',exact=True).click()
    await page.get_by_label('Staff name',exact=True).fill('garden shorthand')
    write_release=asyncio.Event();write_started=asyncio.Event();writes=[]
    async def save(route):
        if route.request.method=='GET':return await route.fallback()
        writes.append(route.request.post_data_json);write_started.set();await write_release.wait()
        await route.fulfill(status=403,content_type='application/json',body='{"error":"Mapping access denied"}')
    await page.route('**/api/products/aliases',save)
    await page.get_by_role('button',name='Save name mapping',exact=True).click();await write_started.wait()
    await expect(page.get_by_label('Staff name',exact=True)).to_be_disabled()
    await expect(combo).to_be_disabled()
    await expect(page.get_by_label('Operations store',exact=True)).to_be_disabled()
    await expect(page.get_by_role('button',name='Hide name mappings',exact=True)).to_be_disabled()
    write_release.set()
    await expect(page.get_by_role('alert').filter(has_text='Mapping access denied')).to_be_visible()
    await expect(page.get_by_label('Staff name',exact=True)).to_have_value('garden shorthand')
    await expect(page.get_by_role('button',name='Save name mapping',exact=True)).to_be_disabled()
    assert writes==[{'product_id':8,'alias':'garden shorthand'}]
    await context.close()


async def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'vite.log').open('w') as log:
        process=subprocess.Popen(['node',str(FRONTEND/'node_modules/vite/bin/vite.js'),'--config',str(HERE/'vite.config.mjs'),'--port','5195'],cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError('Vite test server exited')
                try:
                    _,writer=await asyncio.open_connection('127.0.0.1',5195);writer.close();await writer.wait_closed();break
                except OSError:await asyncio.sleep(.1)
            else:raise RuntimeError('Vite test server did not start')
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                try:
                    for width in (390,1440):
                        await detail_checks(browser,width)
                        await sales_checks(browser,width)
                    await pending_and_race_checks(browser)
                finally:await browser.close()
        finally:process.terminate();process.wait(timeout=10)

if __name__=='__main__':
    asyncio.run(main())
    print('PASS historical sales scope, estimates, deliberate edits and name mappings at 390/1440')
