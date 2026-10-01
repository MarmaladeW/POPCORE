"""Role-aware store entry points with isolated synthetic APIs."""
import asyncio
import subprocess
from pathlib import Path
from playwright.async_api import async_playwright, expect
from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server
from check_trades_today import api_for

OUT = Path(__file__).resolve().parents[3] / '.local/store-navigation/browser'

async def checks(browser):
    for width in (390, 1280):
        state = {'mode': 'data'}
        state['api_payload'] = api_for('manager', state)
        context, page = await context_with_api(browser, state, viewport={'width': width, 'height': 900}, auth={'role': 'manager'})
        await context.add_init_script("localStorage.setItem('popcore_selected_store', JSON.stringify({id:1,code:'DT',name:'Downtown'}))")
        await page.goto(BASE + '/')
        await expect(page.get_by_role('heading', name='Store overview / 门店概览')).to_be_visible()
        await expect(page.get_by_role('link', name='Review sale #41', exact=True)).to_have_attribute('href', '/sales/documents/41')
        actions = page.get_by_role('navigation', name='Store actions')
        for path in ('/checkout', '/incoming', '/claw', '/summary'):
            await expect(actions.locator(f'a[href="{path}"]')).to_be_visible()
        await expect(page.get_by_text('My shifts / 我的班次', exact=True)).to_be_visible()
        await page.screenshot(path=OUT / f'manager-home-{width}.png', full_page=True)
        await page.evaluate("window.__FOUNDATION_AUTH={role:'staff'};window.dispatchEvent(new Event('foundation-auth'))")
        await expect(page.get_by_role('heading', name='开始工作')).to_be_visible()
        await expect(page.get_by_role('link', name='Review sale #41', exact=True)).to_have_count(0)
        await expect(page.get_by_role('navigation', name='Store actions').get_by_role('link')).to_have_count(4)
        await page.get_by_role('link', name='Store tasks', exact=True).click()
        await expect(page).to_have_url(BASE + '/today')
        await page.goto(BASE + '/stock')
        tools = page.get_by_role('navigation', name='Inventory tools')
        for label, path in [('Restock', '/restock'), ('Trades', '/trades'), ('Products', '/products')]:
            await expect(tools.get_by_role('link', name=label, exact=True)).to_have_attribute('href', path)
        await page.goto(BASE + '/summary')
        await expect(page.get_by_role('link', name='Cash count & closing', exact=False)).to_have_attribute('href', '/closing')
        await page.goto(BASE + '/checkout/history')
        nav = page.locator('.pc-bottom-nav' if width == 390 else '.pc-sidebar-menu')
        await expect(nav.locator('a[href="/checkout"]')).to_have_attribute('aria-current', 'page')
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        await context.close()
    context, page = await context_with_api(browser, {'mode':'data', 'api_payload':api_for('viewer', {})}, auth={'role':'viewer'})
    await page.goto(BASE + '/')
    await expect(page.get_by_role('navigation', name='Store actions')).to_have_count(0)
    await expect(page.get_by_role('link', name='Products', exact=True)).to_be_visible()
    await context.close()

async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'vite.log').open('w') as log:
        process = subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--config', str(HERE/'vite.config.mjs')], cwd=FRONTEND, stdout=log, stderr=log)
        try:
            await wait_for_server(process)
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                try:
                    await checks(browser)
                finally:
                    await browser.close()
            print('PASS: staff/manager homes, secondary task access, role change, and navigation at 390/1280.')
        finally:
            process.terminate()
            process.wait(timeout=10)

if __name__ == '__main__':
    asyncio.run(main())
