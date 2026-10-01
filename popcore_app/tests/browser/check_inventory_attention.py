"""Inventory attention views and authenticated reference photos, isolated fixtures."""
import asyncio
import base64
import subprocess
from urllib.parse import parse_qs
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT, context_with_api
from check_design_inventory import payload as design_payload

BASE = 'http://127.0.0.1:5194'
OUT = ROOT / '.local/inventory-attention'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a11sAAAAASUVORK5CYII=')


def payload(path, query, request):
    data = design_payload(path, query, request)
    if path != '/api/inventory/series':
        return data
    store = parse_qs(query).get('store_code', ['DT'])[0]
    data['locations'].append({'id':2,'store_id':1,'store_code':store,'name':'Upstairs','code':'upstairs','opening_verified':True})
    products = data['series'][0]['products']
    for product in products:
        product['balances'].append({'location_id':2,'disposition':'saleable','quantity':5 if product['id']==14 else 0,'version':1})
        if product['id']==13 and store=='DT':
            product['image_filename']='reference moon.png'
        if product['id']==15:
            product['identity_status']='needs_review'
            product['balances']=[{**row,'quantity':None,'version':None} for row in product['balances']]
    products.append({**next(product for product in products if product['id']==14),'id':17,'name':'Star','design_name':'Star','sku':'STAR','balances':[{'location_id':location,'disposition':'saleable','quantity':0,'version':0} for location in (1,2)]})
    return data


async def check(browser,width):
    state={'mode':'data','api_payload':payload}
    context,page=await context_with_api(browser,state,viewport={'width':width,'height':900},auth={'role':'manager','token':'photo-test'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    photo_requests=[]
    async def photo(route):
        photo_requests.append(route.request)
        assert route.request.headers.get('authorization')=='Bearer photo-test'
        assert route.request.url.endswith('/hidden_imgs/reference%20moon.png')
        await route.fulfill(status=404 if len(photo_requests)==2 else 200,content_type='image/png',body=b'broken image' if len(photo_requests)==3 else PNG)
    await page.route('**/hidden_imgs/**',photo)
    await page.goto(BASE+'/stock?series_id=7')
    moon=page.get_by_label('Stock details for Moon',exact=True)
    await expect(moon.locator('img')).to_be_visible()
    assert (await moon.locator('img').get_attribute('src')).startswith('blob:')
    await moon.click()
    await expect(page.get_by_text('Photo unavailable.',exact=False)).to_be_visible()
    await page.get_by_role('button',name='Retry photo',exact=True).click()
    await expect(page.get_by_text('Photo unavailable.',exact=False)).to_be_visible()
    await page.get_by_role('button',name='Retry photo',exact=True).click()
    reference=page.get_by_role('img',name='Reference photo for Moon',exact=True)
    await expect(reference).to_be_visible()
    assert (await reference.get_attribute('src')).startswith('blob:')
    await expect(page.get_by_role('button',name='Move stock',exact=True)).to_be_visible()
    await page.keyboard.press('Escape')
    await expect(page.get_by_role('dialog',name='Stock details',exact=True)).not_to_be_visible()
    status=page.get_by_label('Inventory status',exact=True)
    for value,name in [('out_of_stock','Star'),('replenish','Sun'),('condition','Moon'),('unverified','Display stand')]:
        await status.select_option(value)
        await expect(page.locator('.pc-series-product')).to_have_count(1)
        await expect(page.get_by_label(f'Stock details for {name}',exact=True)).to_be_visible()
    await status.select_option('attention')
    await expect(page.locator('.pc-series-product')).to_have_count(4)
    await status.select_option('all')
    await page.get_by_label('Find design in this series',exact=True).fill('star')
    await expect(page.locator('.pc-series-product')).to_have_count(1)
    await page.get_by_label('Find design in this series',exact=True).fill('')
    await page.get_by_role('heading',name='Confirmed designs',exact=True).scroll_into_view_if_needed()
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert await page.locator('.pc-series-filterbar > .ant-input-affix-wrapper').evaluate('element => Math.abs(element.getBoundingClientRect().height - 44) < 2')
    await page.screenshot(path=OUT/f'inventory-{width}.png',animations='disabled')
    await page.get_by_label('Operations store',exact=True).select_option('ALL')
    await page.get_by_label('Stock details for Moon',exact=True).click()
    await expect(page.get_by_role('button',name='Move stock',exact=True)).to_have_count(0)
    await expect(page.get_by_role('img',name='Reference photo for Moon',exact=True)).to_have_count(0)
    assert all(request['method']=='GET' for request in state['requests'])
    await context.close()


async def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'vite.log').open('w') as log:
        process=subprocess.Popen(['node',str(FRONTEND/'node_modules/vite/bin/vite.js'),'--config',str(HERE/'vite.config.mjs'),'--port','5194'],cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError('Vite test server exited')
                try:
                    _,writer=await asyncio.open_connection('127.0.0.1',5194);writer.close();await writer.wait_closed();break
                except OSError: await asyncio.sleep(.1)
            else: raise RuntimeError('Vite test server did not start')
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                try:
                    for width in (390,768,1440): await check(browser,width)
                finally: await browser.close()
        finally:
            process.terminate();process.wait(timeout=10)

if __name__=='__main__':
    asyncio.run(main())
    print('PASS inventory attention filters, protected photos/retry, read-only all-store scope,390/768/1440px')
