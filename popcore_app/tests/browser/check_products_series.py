"""Products 整端·确认款 Sets & designs with synthetic APIs: product list, stock per location, adding designs on the product."""
import asyncio
import subprocess
from pathlib import Path
from playwright.async_api import async_playwright, expect
from check_foundation import BASE, FRONTEND, HERE, context_with_api, payload, wait_for_server

OUT = Path(__file__).resolve().parents[3] / '.local/products-series/browser'
LOCATIONS = [
    {'id': 11, 'store_id': 1, 'store_code': 'DT', 'code': 'floor', 'name': 'Floor', 'opening_verified': True},
    {'id': 12, 'store_id': 1, 'store_code': 'DT', 'code': 'upstairs', 'name': 'Upstairs', 'opening_verified': True},
]


def product(pid, form, name, design=None, floor=0, upstairs=0):
    return {'id': pid, 'sku': f'SKU-{pid}', 'name': name, 'jizhanming': name, 'series_id': 5, 'stock_form': form,
            'stock_unit': {'sealed_set': 'set', 'random_box': 'box'}.get(form, 'piece'), 'design_name': design,
            'identity_status': 'verified',
            'balances': [{'location_id': 11, 'disposition': 'saleable', 'quantity': floor, 'version': 1},
                         {'location_id': 12, 'disposition': 'saleable', 'quantity': upstairs, 'version': 1}]}


SERIES = {'mode': 'authoritative', 'locations': LOCATIONS, 'unassigned_count': 0, 'series': [
    {'id': 5, 'name': 'Dream Series', 'product_id': 52, 'products': [
        product(51, 'sealed_set', 'Dream 整端', floor=1, upstairs=3),
        product(52, 'random_box', 'Dream 盲盒', floor=2),
        product(53, 'confirmed_design', 'Dream Rabbit', 'Rabbit 兔子', floor=2, upstairs=1),
        product(54, 'confirmed_design', 'Dream Cat', 'Cat 猫', floor=0, upstairs=0),
    ]},
    {'id': 6, 'name': 'Empty Series', 'product_id': None, 'products': []},
]}


def api(path, query, request):
    if path == '/api/inventory/series':
        return SERIES
    if path == '/api/products/52/designs':
        return {'product_id': 52, 'series_id': 5, 'product_ids': [60, 61]}
    return payload(path, query, False)


async def flow(browser, width):
    state = {'mode': 'data', 'api_payload': api}
    context, page = await context_with_api(browser, state, viewport={'width': width, 'height': 900}, auth={'role': 'manager'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store', JSON.stringify({id:1,code:'DT',name:'Downtown'}))")
    try:
        await page.goto(BASE + '/products/series')
        tabs = page.get_by_role('navigation', name='Products sections')
        await expect(tabs.locator('a[href="/products/series"]')).to_have_attribute('aria-current', 'page')
        await expect(tabs.locator('a[href="/products/clover-matching"]')).to_be_visible()
        if width < 768:
            await page.get_by_role('button', name='Dream Series', exact=False).click()
        detail = page.get_by_role('region', name='Dream Series')
        await expect(detail.get_by_text('Rabbit 兔子', exact=True)).to_be_visible()
        # Figures: 2 confirmed designs, 1 sealed set, 1 single box, design stock 3 (floor 2 · upstairs 1).
        figures = detail.locator('.pc-figures > div')
        await expect(figures.nth(0).locator('strong')).to_have_text('2')
        await expect(figures.nth(1).locator('strong')).to_have_text('1')
        await expect(figures.nth(2).locator('strong')).to_have_text('1')
        await expect(figures.nth(3).locator('strong')).to_have_text('3')
        await expect(figures.nth(3).get_by_text('楼面 2 · 楼上 1', exact=True)).to_be_visible()
        rabbit = detail.get_by_role('row').filter(has_text='Rabbit 兔子')
        await expect(rabbit.locator('td').nth(-1)).to_contain_text('3')

        # The set already has its 整端, so only designs can be added.
        await expect(detail.get_by_role('button', name='+ 整端 Add sealed set')).to_have_count(0)
        await detail.get_by_role('button', name='+ 确认款 Add designs').click()
        names = page.get_by_role('textbox', name='Design names, one per line')
        save = detail.get_by_role('button', name='Save', exact=False)
        await names.fill('Bear 熊\nbear 熊')
        await expect(page.get_by_text('Each design name must be unique.', exact=True)).to_be_visible()
        await expect(save).to_be_disabled()
        await names.fill('Bear 熊\nFox 狐狸')
        await save.click()
        await expect(names).to_have_count(0)
        posts = [r for r in state['requests'] if r['path'] == '/api/products/52/designs' and r['method'] == 'POST']
        assert len(posts) == 1 and posts[0]['idempotency_key'], posts
        assert posts[0]['post_data'] == {'design_names': ['Bear 熊', 'Fox 狐狸']}, posts
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        await page.screenshot(path=OUT / f'series-{width}.png', full_page=True)
    finally:
        await context.close()

    # Staff see the sets and designs but cannot add any.
    state = {'mode': 'data', 'api_payload': api}
    context, page = await context_with_api(browser, state, viewport={'width': width, 'height': 900}, auth={'role': 'staff'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store', JSON.stringify({id:1,code:'DT',name:'Downtown'}))")
    try:
        await page.goto(BASE + '/products/series?series_id=5')
        await expect(page.get_by_role('region', name='Dream Series').get_by_text('Cat 猫', exact=True)).to_be_visible()
        await expect(page.get_by_role('button', name='+ 确认款 Add designs')).to_have_count(0)
        await expect(page.get_by_role('button', name='新建系列 New series')).to_have_count(0)
        await expect(page.get_by_role('navigation', name='Products sections').locator('a[href="/products/clover-matching"]')).to_have_count(0)
    finally:
        await context.close()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'vite.log').open('w') as log:
        process = subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--config', str(HERE / 'vite.config.mjs')], cwd=FRONTEND, stdout=log, stderr=log)
        try:
            await wait_for_server(process)
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                try:
                    for width in (390, 1280):
                        await flow(browser, width)
                finally:
                    await browser.close()
            print('PASS: Products sets & designs, stock per location, adding designs on the product and staff limits at 390/1280.')
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
