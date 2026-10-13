"""Real Flask design identification and ambiguous-response retry using disposable data."""
import asyncio
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
OUT = ROOT / '.local/design-inventory/integration'
BASE = 'http://127.0.0.1:5188'
sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
from test_design_identification import DesignIdentificationTests


async def checks(browser, fixture):
    context = await browser.new_context(viewport={'width':390,'height':844})
    await context.add_init_script("window.__FOUNDATION_AUTH={role:'staff',token:'staff'};localStorage.setItem('popcore_selected_store',JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    page = await context.new_page()
    async def local_only(route):
        if urlsplit(route.request.url).hostname not in ('127.0.0.1','localhost'):
            await route.abort()
        else:
            await route.continue_()
    await page.route('**/*',local_only)
    requests = []
    async def uncertain_identify(route):
        requests.append((route.request.post_data_json,route.request.headers.get('idempotency-key')))
        response = await route.fetch()
        assert response.status==200,await response.text()
        if len(requests)==1:
            await route.abort('failed')
        else:
            await route.fulfill(response=response)
    await page.route('**/api/goods/identify',uncertain_identify)
    await page.goto(BASE+f'/stock?series_id={fixture.series_id}')
    await expect(page.get_by_text('5 boxes',exact=True)).to_be_visible()
    await expect(page.get_by_text('0 pieces',exact=True)).to_be_visible()
    await page.get_by_role('button',name='Identify boxes',exact=True).click()
    await expect(page.get_by_role('dialog',name='Identify boxes',exact=True)).to_be_visible()
    await page.get_by_label('Source boxes',exact=True).select_option(str(fixture.product_id))
    await page.get_by_label('Named design',exact=True).select_option(str(fixture.design_ids[0]))
    await page.get_by_label('Location',exact=True).select_option(str(fixture.floor))
    await page.get_by_label('Identification quantity',exact=True).fill('2')
    await page.get_by_label('Identification reason',exact=True).fill('Physically confirmed Design A')
    await page.get_by_role('button',name='Confirm identification',exact=True).click()
    await expect(page.get_by_text('Unable to confirm this request. Retry to check its result.',exact=True)).to_be_visible()
    await expect(page.get_by_label('Operations store',exact=True)).to_be_disabled()
    await page.keyboard.press('Escape')
    await expect(page.get_by_role('dialog')).to_be_visible()
    assert fixture.quantities()=={fixture.product_id:3,fixture.design_ids[0]:2,fixture.design_ids[1]:7}
    await page.get_by_role('button',name='Retry',exact=True).click()
    await expect(page.get_by_text('Identification saved. Inventory refreshed.',exact=True)).to_be_visible()
    assert len(requests)==2 and requests[0]==requests[1] and requests[0][1]
    await expect(page.get_by_text('3 boxes',exact=True)).to_be_visible()
    await expect(page.get_by_text('2 pieces',exact=True)).to_be_visible()
    await expect(page.get_by_text('7 pieces',exact=True)).to_be_visible()
    await page.get_by_role('button',name='Show movement history',exact=True).click()
    await expect(page.get_by_role('dialog',name='Movement history',exact=True)).to_be_visible()
    history=page.locator('.pc-history-movements')
    await expect(history.locator('li')).to_have_count(2)
    await expect(history.get_by_text('Identification Series · A',exact=True)).to_be_visible()
    await expect(page.get_by_role('dialog')).to_be_visible()
    await page.keyboard.press('Escape')
    await expect(page.get_by_role('dialog')).not_to_be_visible()
    for width in (390,768,1440):
        await page.set_viewport_size({'width':width,'height':1000})
        await page.get_by_role('heading',name='Confirmed designs',exact=True).scroll_into_view_if_needed()
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(animations='disabled',path=str(OUT/f'identified-{width}.png'))
        await page.get_by_label('Stock details for A',exact=True).click()
        drawer=page.get_by_role('dialog',name='Stock details',exact=True)
        await expect(drawer).to_be_in_viewport(ratio=.99)
        await expect(drawer.get_by_text('Saleable: 2 pieces',exact=True)).to_be_visible()
        assert await drawer.evaluate('element => element.scrollWidth <= element.clientWidth')
        await page.screenshot(animations='disabled',path=str(OUT/f'identified-details-{width}.png'))
        await page.keyboard.press('Escape')
        await expect(drawer).not_to_be_visible()
    with closing(fixture.connect()) as con:
        assert con.execute('SELECT COUNT(*) FROM inventory_documents').fetchone()[0]==2
        assert con.execute('SELECT COUNT(*) FROM trade_units').fetchone()[0]==0
    await context.close()


async def main():
    OUT.mkdir(parents=True,exist_ok=True)
    fixture=DesignIdentificationTests();fixture.setUp()
    server=make_server('127.0.0.1',5059,fixture.app)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    config=OUT/'vite.mjs'
    config.write_text(f"import config from {json.dumps(str(HERE/'vite.config.mjs'))}; export default {{...config,server:{{...config.server,port:5188,proxy:{{'/api':'http://127.0.0.1:5059'}}}}}}")
    with (OUT/'vite.log').open('w') as log:
        process=subprocess.Popen(['node','node_modules/vite/bin/vite.js','--config',str(config)],cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError('Vite test server exited')
                try:
                    _,writer=await asyncio.open_connection('127.0.0.1',5188);writer.close();await writer.wait_closed();break
                except OSError:await asyncio.sleep(.1)
            else:raise RuntimeError('Vite test server did not start')
            async with async_playwright() as pw:
                browser=await pw.chromium.launch()
                try:await checks(browser,fixture)
                finally:await browser.close()
        finally:
            process.terminate();process.wait(timeout=10)
            server.shutdown();server.server_close();thread.join(timeout=5)
            fixture.tearDown();fixture.doCleanups()


if __name__=='__main__':
    asyncio.run(main())
    print('PASS: real Flask design identification, committed-response loss/retry, exact balances and history, mobile/desktop.')
