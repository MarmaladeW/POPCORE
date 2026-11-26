"""记一笔 Quick sale in a real browser against the real Flask routes and a disposable database (owner, 2026-11-21: it
replaces 随手记): a cashier finds products by 记账名, the server prices the cart, one tender and Save record the whole
sale and move no stock; an e-transfer saved without a photo says 没有照片 until one is uploaded; 撤销 takes a sale back;
a sale ticked 员工折扣 names who bought and counts their use; then a manager reviews the day (审核): the preview shows
what comes off, the cash counted goes in one box, confirming takes the stock off once, with no 日报 pasted (owner,
2026-11-24); nothing scrolls sideways, at 390 and 1440 px."""
import asyncio
import io
import re
import subprocess
import sys
from contextlib import closing
from urllib.parse import urlsplit

from PIL import Image
from playwright.async_api import async_playwright, expect

from check_foundation import FRONTEND, HERE, ROOT

PORT = 5193
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'quick-sale'


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_quick_sale import QuickSaleFixture
    fixture = QuickSaleFixture('run')
    fixture.setUp()
    with closing(fixture.connect()) as con:
        # A live store, as DT and MK are: its opening count is a posted document, so a review takes stock.
        opening = con.execute("""INSERT INTO inventory_documents(kind, request_key, payload_hash, actor_sub, business_date, status)
                                 VALUES ('opening', 'check-opening', 'x', 'auth0|admin', '2026-11-01', 'posted')""").lastrowid
        con.execute('UPDATE inventory_scope_state SET opening_verified=1, opening_document_id=? WHERE store_id=?',
                    (opening, fixture.store_id))
        # 正式员工: Amy has 员工折扣 uses.
        con.execute("UPDATE employees SET rank='regular' WHERE name='Amy'")
        con.commit()
    contexts = []

    async def open_as(role):
        context = await browser.new_context(viewport={'width': width, 'height': 900},
                                            permissions=['clipboard-read', 'clipboard-write'])
        contexts.append(context)
        await context.add_init_script(
            f"window.__FOUNDATION_AUTH={{role:'{role}',token:'{role}'}};"
            f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.store_id},code:'DT',name:'Downtown'}}));")
        opened = await context.new_page()

        async def flask_boundary(route):
            parsed = urlsplit(route.request.url)
            if parsed.hostname not in ('127.0.0.1', 'localhost'):
                await route.abort(); return
            if not parsed.path.startswith('/api/'):
                await route.continue_(); return
            headers = {'Authorization': f'Bearer {role}'}
            if route.request.headers.get('idempotency-key'):
                headers['Idempotency-Key'] = route.request.headers['idempotency-key']
            kind = route.request.headers.get('content-type') or ''
            extra = ({'data': route.request.post_data_buffer, 'content_type': kind} if 'multipart' in kind
                     else {'json': route.request.post_data_json} if route.request.post_data else {})
            response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                           method=route.request.method, headers=headers, **extra)
            await route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
        await opened.route('**/*', flask_boundary)
        return opened

    try:
        page = await open_as('staff')

        with closing(fixture.connect()) as con:
            con.execute("INSERT INTO products(sku,name_cn_en,jizhanming,identity_status) VALUES ('AC0013','Doll clothes','娃衣 $20','unverified')")
            con.commit()

        # The staff home's big tile opens 记一笔.
        await page.goto(BASE + '/')
        await page.get_by_role('link', name='记一笔', exact=False).first.click()
        await expect(page).to_have_url(BASE + '/sales/quick')
        search = page.get_by_label('记账名、名字或编号 Name or code')
        # A product nobody reviewed yet shows greyed, saying who fixes it, and cannot be added.
        await search.fill('娃衣')
        unreviewed = page.get_by_role('list', name='Products found').get_by_role('button').filter(has_text='娃衣')
        await expect(unreviewed).to_be_disabled()
        await expect(unreviewed).to_contain_text('还没核对，告诉店长')
        await search.fill('蒙奇')
        await page.get_by_role('list', name='Products found').get_by_role('button').first.click()
        await search.fill('santa')
        found = page.get_by_role('list', name='Products found')
        await found.get_by_role('button').filter(has_text='SA Santa').filter(has_not_text='Reindeer').first.click()
        cart = page.get_by_role('list', name='Items')
        await expect(cart.get_by_role('listitem')).to_have_count(2)
        await page.get_by_role('group', name='SA Santa quantity').get_by_role('button', name='More').click()
        # $20 + 2 × $14.99 = $49.98, HST $6.50: the card tile shows $56.48, the cash tile the cash price $55; one price each.
        await expect(page.get_by_role('radio', name='刷卡', exact=False)).to_contain_text('$56.48')
        cash = page.get_by_role('radio', name='现金', exact=False)
        await expect(cash).to_contain_text('$55.00')
        # Staff do not change a listed price (owner, 2026-11-26): the line shows it, with no 改价.
        await expect(cart.get_by_text('$14.99', exact=True)).to_be_visible()
        await expect(cart.get_by_role('button', name=re.compile('改价'))).to_have_count(0)
        # Nor give a discount without a named 员工折扣 buyer: the + 优惠 box waits for one.
        await expect(page.get_by_role('button', name='+ 优惠', exact=True)).to_have_count(0)
        await expect(page.get_by_role('button', name='+ 员工折扣', exact=True)).to_be_visible()
        await cash.click()
        await fits(page, f'cart-{width}.png')
        await page.get_by_role('button', name=re.compile(r'^保存 · 现金.*\$55\.00$')).click()
        await expect(page.get_by_role('status', name='Saved')).to_contain_text('现金 $55.00')
        # No stock moves until the day's 日报 is reviewed (owner, 2026-11-21).
        assert (fixture.balance(fixture.product_id), fixture.balance(fixture.head)) == (5, 10)

        # E-transfer: saved without a photo, flagged until one is uploaded.
        await search.fill('蒙奇')
        await page.get_by_role('list', name='Products found').get_by_role('button').first.click()
        await page.get_by_role('radio', name='E-transfer', exact=False).click()
        save = page.get_by_role('button', name=re.compile(r'^保存 · E-transfer \$21\.00'))
        await expect(save).to_contain_text('然后拍付款截图')
        await fits(page, f'emt-{width}.png')
        await save.click()
        saved = page.get_by_role('status', name='Saved')
        await expect(saved).to_contain_text('E-transfer $21.00')
        await expect(saved.get_by_role('button', name='拍付款截图')).to_be_visible()
        await fits(page, f'saved-photo-{width}.png')
        await saved.get_by_role('button', name='稍后再拍').click()
        today = page.get_by_role('list', name='Sales today')
        await expect(today.get_by_role('listitem')).to_have_count(2)
        flagged = today.get_by_role('listitem').filter(has_text='没照片')
        await expect(flagged).to_have_count(1)
        await fits(page, f'no-photo-{width}.png')
        image = io.BytesIO(); Image.new('RGB', (16, 16), 'green').save(image, format='PNG')
        await flagged.get_by_label('Upload payment photo').set_input_files(
            {'name': 'emt.png', 'mimeType': 'image/png', 'buffer': image.getvalue()})
        await expect(today.get_by_text('没照片')).to_have_count(0)

        # 复制汇总: the day as the 日报 writes it.
        await page.get_by_role('button', name='复制汇总').click()
        await expect(page.get_by_label('Day summary')).to_be_visible()
        copied = await page.evaluate('navigator.clipboard.readText()')
        assert copied == '2026.11.21 DT\n\n卡机汇总：\n\n随手记汇总：\n蒙奇奇*1，SA Santa*2\n蒙奇奇*1\n', copied
        await fits(page, f'today-{width}.png')
        # 撤销 the first sale (recorded by mistake): it stays, struck through, and leaves the total and the summary.
        first = today.get_by_role('listitem').filter(has_text='SA Santa*2')
        await first.get_by_role('button', name='撤销').click()
        confirm = page.get_by_role('tooltip').filter(has_text='撤销这一笔？')
        await confirm.get_by_label('Why').fill('应该是刷卡')
        await confirm.get_by_role('button', name='撤销').click()
        await expect(first).to_contain_text('已撤销 · Xiao Chen · 应该是刷卡')
        await expect(page.get_by_role('region', name="Today's sales")).to_contain_text('1 笔 · $21.00')
        # The list is the cashier's own sales, and says so.
        await expect(page.get_by_role('region', name="Today's sales")).to_contain_text('今天 · 我记的')
        await page.get_by_role('button', name='复制汇总').click()
        assert await page.evaluate('navigator.clipboard.readText()') == '2026.11.21 DT\n\n卡机汇总：\n\n随手记汇总：\n蒙奇奇*1\n'
        # Staff see where the day stands and have no review button.
        await expect(page.get_by_role('region', name='Review the day')).to_contain_text('库存还没扣，店长审核后一起扣')
        await expect(page.get_by_role('button', name='审核并扣库存')).to_have_count(0)
        await fits(page, f'undone-{width}.png')
        with closing(fixture.connect()) as con:
            lines = [row[0] for row in con.execute(
                "SELECT detail FROM activity_log WHERE endpoint='checkout.quick_sale_save' ORDER BY id")]
        assert lines == ['现金 Cash $55.00 · 蒙奇奇*1，SA Santa*2', 'E-transfer $21.00 · 蒙奇奇*1 · 没有照片 No photo'], lines
        assert (fixture.balance(fixture.product_id), fixture.balance(fixture.head)) == (5, 10)

        # 员工折扣: a tick, then who bought; Save waits for the name. The sale is a sale like any other.
        await search.fill('蒙奇')
        await page.get_by_role('list', name='Products found').get_by_role('button').first.click()
        await page.get_by_role('button', name='+ 员工折扣').click()
        buyers = page.get_by_role('group', name='Staff discount')
        await page.get_by_role('radio', name='现金', exact=False).click()
        await expect(page.get_by_role('button', name='选哪位员工买的')).to_be_disabled()
        # Names only: how many uses someone has left is theirs to see (owner, 2026-11-26).
        await expect(buyers.get_by_role('button', name='Amy')).to_be_visible()
        await expect(buyers).not_to_contain_text('剩')
        await buyers.get_by_role('button', name='Amy').click()
        await expect(page.get_by_role('button', name='+ 优惠', exact=True)).to_be_visible()
        await fits(page, f'staff-discount-{width}.png')
        await page.get_by_role('button', name=re.compile(r'^保存 · 现金 \$21\.00$')).click()
        await expect(page.get_by_role('status', name='Saved')).to_contain_text('员工折扣 Amy')
        await expect(today.get_by_role('listitem').filter(has_text='员工折扣 Amy')).to_have_count(1)
        uses = fixture.client.get('/api/staff-discounts', headers={'Authorization': 'Bearer manager'}).get_json()
        assert [(use['name'], use['uses'], use['payment'], use['amount_cents']) for use in uses['uses']] == [('Amy', 1, 'cash', 2100)], uses['uses']

        # 审核: the manager looks over the day and confirms; the two sales that stand come off the floor, once.
        boss = await open_as('manager')
        await boss.goto(BASE + '/sales/quick')
        await expect(boss.get_by_role('region', name="Today's sales")).not_to_contain_text('我记的')
        review = boss.get_by_role('region', name='Review the day')
        await review.get_by_role('button', name='审核并扣库存').click()
        moves = review.get_by_role('group', name='What this review moves')
        await expect(moves).to_contain_text('从楼下扣 2')
        assert fixture.balance(fixture.product_id) == 5   # a preview moves nothing
        # The cash counted that day goes in the one box beside the day's cash sales; it may stay empty.
        await expect(moves).to_contain_text('现金销售 $21.00')
        await moves.get_by_label('Cash counted').fill('120.5')
        await fits(boss, f'review-{width}.png')
        await moves.get_by_role('button', name='确认扣库存').click()
        await expect(review).to_contain_text('已审核，库存已扣')
        await expect(review).to_contain_text('现金 $120.50')
        await expect(review.get_by_role('button', name='审核并扣库存')).to_have_count(0)
        assert (fixture.balance(fixture.product_id), fixture.balance(fixture.head)) == (3, 10)
        await fits(boss, f'reviewed-{width}.png')
        with closing(fixture.connect()) as con:
            sold = [tuple(row) for row in con.execute("SELECT product_id, qty_cash, qty_sold FROM daily_sales WHERE date='2026-11-21'")]
        assert sold == [(fixture.product_id, 2, 2)], sold
    finally:
        for context in contexts:
            await context.close()
        fixture.doCleanups()
        fixture.tearDown()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'vite.log').open('w') as log:
        process = subprocess.Popen(['node', str(FRONTEND / 'node_modules/vite/bin/vite.js'), '--config',
                                    str(HERE / 'vite.config.mjs'), '--port', str(PORT)],
                                   cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Vite stopped')
                try:
                    _, writer = await asyncio.open_connection('127.0.0.1', PORT)
                    writer.close(); await writer.wait_closed(); break
                except OSError:
                    await asyncio.sleep(.1)
            else:
                raise RuntimeError('Vite did not start')
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    for width in (390, 1440):
                        await run(browser, width)
                finally:
                    await browser.close()
        finally:
            process.terminate(); process.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
    print('PASS: 记一笔 Quick sale: found by 记账名, priced by the server, saved in one tap with no stock moved; an '
          'e-transfer without a photo flagged until one is uploaded; 撤销 takes a sale back; 员工折扣 names who bought and '
          'counts the use; a manager reviews the day, writes the cash counted, and its stock comes off once, with no '
          '日报 pasted; at 390 and 1440 px')
