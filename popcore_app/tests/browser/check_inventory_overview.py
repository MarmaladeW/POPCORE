"""库存 overview and list quantities on the inventory landing page, isolated fixtures."""
import asyncio
import re
import subprocess
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT, context_with_api
from fixtures import payload as base_payload

PORT = 5197
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'inventory-overview'
LOCATIONS = [{'id':1,'store_id':1,'store_code':'DT','name':'Floor','code':'floor','opening_verified':True},
             {'id':2,'store_id':1,'store_code':'DT','name':'Upstairs','code':'upstairs','opening_verified':True}]


def product(pid, name, form, unit, floor, back, series_id=None, status='verified'):
    known = status == 'verified'
    return {'id':pid,'sku':f'PM{pid:04d}','name':name,'series_id':series_id,'stock_form':form,'stock_unit':unit,
            'design_name':name if form=='confirmed_design' else None,'identity_status':status,'open_sets':[],
            'balances':[{'location_id':location,'disposition':'saleable','quantity':quantity if known else None,'version':1 if known else None}
                        for location,quantity in ((1,floor),(2,back))]}


def item(pid, name, **values):
    return {'product_id':pid,'store_code':'DT','name':name,'sku':f'PM{pid:04d}','unit':'box','link':{'series_id':88} if pid<300 else {'product_id':pid},
            'counted':True,'floor':0,'back':0,'back_sets':0,'set_size':12,'sold_week':0,'sold_recent':0,'last_sold':None,'pace':0,'days_left':None,**values}


REFILL = [item(200+index, name, back_sets=sets) for index,(name,sets) in enumerate(
    [('Dimoo奇遇figure',4),('间谍过家家日常',2),('Dimoo梦里梦外figure',15),('Dimoo Retro',2),('Kubo 呼吸',2),('白雪公主',7)])]


def payload(path, query, request):
    if path == '/api/inventory/overview':
        return {'live':True,'stores':['DT'],'today':'2026-11-01','in_stock_products':1264,'report_days':3,'last_report':'2026-10-31',
                'figures':[{'key':'floor','codes':['floor'],'units':7134,'products':493,'by_unit':{'box':5194,'piece':1940}},
                           {'key':'back','codes':['upstairs'],'units':3770,'products':656,'by_unit':{'set':1821,'piece':1940,'box':9}},
                           {'key':'display','codes':['display'],'units':569,'products':549,'by_unit':{'piece':279,'box':261,'set':29}},
                           {'key':'claw','codes':['claw'],'units':175,'products':25,'by_unit':{'box':175}}],
                'refill':REFILL,'refill_count':len(REFILL),
                'running_low':[item(301,'小野暗场毛绒',floor=4,pace=4,days_left=1.0,sold_week=12),item(302,'ETB-PE',floor=4,back=11,pace=2,days_left=2.0,unit='piece')],
                'sold_out':[item(303,'间谍过家家Q版挂饰',sold_recent=5,last_sold='2026-10-29')],
                'best_sellers':[item(304,'Smiski Series 2',floor=38,back_sets=1,sold_week=14),item(305,'未设类型商品',counted=False,sold_week=3)],
                'recent_changes':[{'business_date':'2026-10-31','kind':'report','store_code':'DT','added':69,'removed':165,'documents':10},
                                  {'business_date':'2026-10-30','kind':'move','store_code':'DT','added':24,'removed':24,'documents':2},
                                  {'business_date':'2026-10-29','kind':'opening','store_code':'DT','added':11744,'removed':0,'documents':1}],
                'not_set_up':289}
    if path == '/api/inventory/series':
        series_products = [product(88,'Dimoo奇遇figure','random_box','box',0,0,88),product(89,'Dimoo奇遇figure 整端','sealed_set','set',0,4,88),
                           product(90,'Pink','confirmed_design','piece',1,0,88),product(91,'Blue','confirmed_design','piece',0,0,88)]
        plain = [product(400,'Smiski Series 2','random_box','box',38,0),product(401,'$100 Gift Card',None,None,0,0,status='unverified')]
        plain[0]['variations'] = [product(402,'Smiski Series 2 整端','sealed_set','set',0,1)]
        plain[0]['created_at'] = '2026-10-31 15:00:00'
        plain[1]['variations'] = []
        return {'mode':'authoritative','locations':LOCATIONS,'unassigned_count':0,
                'series':[{'id':88,'name':'Dimoo奇遇figure','product_id':88,'products':series_products}],'products':plain}
    if path == '/api/inventory/locations':
        return LOCATIONS
    return base_payload(path, query)


async def check(browser, width):
    context,page = await context_with_api(browser,{'mode':'data','api_payload':payload},viewport={'width':width,'height':900},auth={'role':'staff'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE+'/stock')
    overview = page.get_by_role('region',name='库存概况 Inventory overview',exact=True)
    await expect(overview).to_be_visible()
    await expect(overview.get_by_text('7,134',exact=True)).to_be_visible()
    await expect(overview.get_by_text('9 盒 · 1,821 端 · 1,940 个',exact=True)).to_be_visible()
    await expect(overview.get_by_text('1,264 products in stock',exact=False)).to_be_visible()
    refill = overview.get_by_role('region',name='补货上架',exact=False)
    rows = refill.get_by_role('listitem')
    await expect(rows).to_have_count(3 if width<768 else 5)
    await refill.get_by_role('button',name='Show all 6',exact=True).click()
    await expect(rows).to_have_count(6)
    await expect(refill.get_by_role('button',name='Show fewer',exact=True)).to_have_attribute('aria-expanded','true')
    await expect(rows.first).to_contain_text('4 端')
    low = overview.get_by_role('region',name='快卖完',exact=False)
    await expect(low.get_by_text('Sells 4 a day · none upstairs',exact=True)).to_be_visible()
    await expect(low.get_by_text('about 1 day',exact=True)).to_be_visible()
    await expect(overview.get_by_text('Not counted yet',exact=True)).to_be_visible()
    changes = overview.get_by_role('region',name='最近变动',exact=False)
    await expect(changes.get_by_text('−96',exact=True)).to_be_visible()
    await expect(changes.get_by_text('moved',exact=True)).to_be_visible()
    await expect(changes.get_by_text('+11,744',exact=True)).to_be_visible()
    # The product list: floor and upstairs numbers, a blind box counting its whole sets upstairs, quiet words otherwise.
    listing = page.locator('.pc-series-list')
    dimoo = listing.get_by_role('link',name=re.compile('^Dimoo奇遇figure'))
    await expect(dimoo).to_contain_text('楼面空 Floor empty')
    await expect(dimoo).to_contain_text('楼上 4 端')
    await expect(dimoo).to_contain_text('1 of 2 designs in stock')
    smiski = listing.get_by_role('link',name=re.compile('^Smiski Series 2'))
    await expect(smiski).to_contain_text('楼面 38')
    await expect(smiski).not_to_contain_text('Floor empty')
    await expect(listing.get_by_role('link',name=re.compile('^\\$100 Gift Card'))).to_contain_text('No stock type')
    # Newest added first by default (higher product numbers were added later), remembered per browser.
    names = listing.locator('.pc-series-list-name')
    await expect(names).to_have_count(3)
    first_words = lambda texts: [text.split()[0] for text in texts]
    assert first_words(await names.all_inner_texts()) == ['$100', 'Smiski', 'Dimoo奇遇figure'], await names.all_inner_texts()
    await expect(smiski).to_contain_text('PM0400 · 添加 10-31')
    order = page.get_by_label('List order',exact=True)
    await order.select_option('oldest')
    await expect(names.first).to_contain_text('Dimoo奇遇figure')
    assert first_words(await names.all_inner_texts()) == ['Dimoo奇遇figure', 'Smiski', '$100']
    await order.select_option('name')
    await expect(names.first).to_contain_text('$100 Gift Card')
    assert first_words(await names.all_inner_texts()) == ['$100', 'Dimoo奇遇figure', 'Smiski']
    await page.reload()
    await expect(page.get_by_label('List order',exact=True)).to_have_value('name')
    await page.get_by_label('List order',exact=True).select_option('newest')
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'horizontal overflow at {width}px'
    await page.screenshot(path=OUT/f'overview-{width}.png',full_page=True,animations='disabled')
    await changes.scroll_into_view_if_needed()
    await page.screenshot(path=OUT/f'overview-lower-{width}.png',animations='disabled')
    await listing.scroll_into_view_if_needed()
    await page.screenshot(path=OUT/f'list-{width}.png',animations='disabled')
    # A search hides the overview so the matches come first.
    await page.get_by_label('Search series or design',exact=True).fill('smiski')
    await page.get_by_label('Search series or design',exact=True).press('Enter')
    await expect(overview).not_to_be_visible()
    # Opening a refill row goes to that product's stock.
    await page.get_by_label('Search series or design',exact=True).fill('')
    await page.get_by_label('Search series or design',exact=True).press('Enter')
    await overview.get_by_role('link',name=re.compile('^Dimoo奇遇figure')).first.click()
    await expect(page).to_have_url(re.compile(r'/stock\?series_id=88$'))
    await context.close()


async def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'vite.log').open('w') as log:
        process=subprocess.Popen(['node',str(FRONTEND/'node_modules/vite/bin/vite.js'),'--config',str(HERE/'vite.config.mjs'),'--port',str(PORT)],cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError('Vite test server exited')
                try:
                    _,writer=await asyncio.open_connection('127.0.0.1',PORT);writer.close();await writer.wait_closed();break
                except OSError: await asyncio.sleep(.1)
            else: raise RuntimeError('Vite test server did not start')
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                try:
                    for width in (390,768,1440): await check(browser,width)
                finally: await browser.close()
        finally:
            process.terminate();process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
    print('PASS inventory overview figures, panels, show all, recent changes, list quantities, search and links, 390/768/1440px')
