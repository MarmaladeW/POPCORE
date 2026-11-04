"""Browser regression for 名称记忆 Name picks and the pick states in the 日报 paste review (synthetic APIs).

The names are the ones the owner answered in chat for DT's Nov 1-2 reports (2026-11-03); products and
counts are synthetic. Screenshots at 1440 and 390 px go to .local/name-picks/browser/."""
import asyncio
import re
import subprocess
from pathlib import Path
from urllib.parse import parse_qs

from playwright.async_api import async_playwright, expect
from check_foundation import BASE, FRONTEND, HERE, context_with_api, wait_for_server
from fixtures import payload as foundation_payload

OUT = Path(__file__).resolve().parents[3] / '.local' / 'name-picks' / 'browser'


def product(pid, sku, shop, name, form='random_box'):
    return {'id': pid, 'sku': sku, 'jizhanming': shop, 'name_cn_en': name, 'stock_form': form, 'stock_unit': 'box',
            'series_name': None, 'ip_series': None, 'design_name': None, 'price': 18}


S1 = product(33, 'DR0033', 'Smiski Series 1', 'Smiski 系列1')
S2 = product(34, 'DR0034', 'Smiski Series 2', 'Smiski 系列2')
BALL1 = product(38, 'DR0038', 'Smiski球 S1', 'Smiski 球 S1 挂件')
BALL2 = product(35, 'DR0035', 'Smiski球 S2', 'Smiski 球 S2 挂件')
LADUREE = product(60, 'DR0060', 'SA Laduree', 'Christmas Ornament Laduree')
KISS = product(78, 'DR0078', 'SA 亲亲', 'Sonny Angel Kiss')
SANTA = product(126, 'DR0126-02', 'SA Christmas 2015 Santa Claus', 'Sonny Angel Christmas 2015 Santa Claus', 'confirmed_design')
DREAM = product(90, 'DR0090', 'SA dream', 'Sonny Angel Dream')
SPECIAL_A = product(3696, 'SO-1', '特殊订单 A', 'Special order A', 'ordinary')
SPECIAL_B = product(3709, 'SO-2', '特殊订单 B', 'Special order B', 'ordinary')
CATALOGUE = [S1, S2, BALL1, BALL2, LADUREE, KISS, SANTA, DREAM]


def place(section, store='DT', display=False):
    return {'store_code': store, 'section': section, 'display': display}


def pick(state, products, places=(), raw='', note='', picked='2026-11-02 23:41:00'):
    return {'state': state, 'raw_name': raw, 'note': note, 'products': list(products), 'places': list(places), 'picked_at': picked}


def name(raw, section, status, qty=1, guess=None, picked=None, box=None, display=False, note=''):
    return {'name_key': raw.casefold(), 'note_key': note.casefold(), 'raw_name': raw, 'note': note, 'section': section,
            'display': display, 'qty': qty, 'box_size': box, 'loose_qty': 0, 'status': status, 'guess': guess, 'pick': picked}


def day_names(state):
    names = [
        name('smiski s1', 'pos', 'open', 2, BALL1),
        name('smiski s2', 'pos', 'open', 1, BALL2),
        name('SA Laduree', 'pos', 'confirm', 1, LADUREE, pick('ok', [LADUREE], [place('cash')], 'SA Laduree')),
        name('SA kiss', 'cash', 'open', 1),
        name('SA Christmas Series 2015-Santa Claus', 'refill', 'stale', 1, SANTA,
             pick('stale', [SANTA], [place('refill', display=True)], 'SA Christmas Series 2015-Santa Claus'), box=1, display=True),
        name('SA dream', 'refill', 'remembered', 2, DREAM, pick('ok', [DREAM], [place('refill')], 'SA dream'), box=12),
        name('娃衣 恐龙', 'stock_in', 'open', 1),
        name('可乐figure', 'stock_out', 'ignored', 1),
    ]
    for entry in names:  # answered names come back quiet, as the server would return them
        if entry['name_key'] in state.get('answered', {}):
            chosen = state['answered'][entry['name_key']]
            entry.update(status='remembered', pick=pick('ok', [chosen], [place(entry['section'])], entry['raw_name']))
        if entry['name_key'] in state.get('ignored', set()):
            entry['status'] = 'ignored'
    return names


def counts(names):
    result = dict.fromkeys(('open', 'disputed', 'stale', 'confirm', 'remembered', 'ignored'), 0)
    for entry in names:
        result[entry['status']] += 1
    return result


PICKS = [
    {'name_key': 'sa dream', 'note_key': '', **pick('ok', [DREAM], [place('refill'), place('pos')], 'SA dream')},
    {'name_key': 'sa laduree', 'note_key': '', **pick('ok', [LADUREE], [place('cash')], 'SA Laduree')},
    {'name_key': 'sa christmas series 2015-santa claus', 'note_key': '',
     **pick('stale', [SANTA], [], 'SA Christmas Series 2015-Santa Claus')},
    {'name_key': 'sa特殊订单', 'note_key': '', **pick('disputed', [SPECIAL_A, SPECIAL_B], [], 'SA特殊订单', picked=None)},
    {'name_key': 'smiski hipper', 'note_key': '', **pick('ok', [S2], [], 'smiski hipper', picked='2026-10-30 21:02:00')},
]


def api_for(state):
    def api_payload(path, query, request):
        if path == '/api/sales/name-picks' and request.method == 'GET':
            names = day_names(state)
            return {'picks': PICKS, 'days': [
                {'store_code': 'DT', 'date': '2026-11-02', 'parsed_at': '2026-11-03 21:10:00', 'saved': False, 'counts': counts(names)},
                {'store_code': 'MK', 'date': '2026-11-02', 'parsed_at': '2026-11-03 20:40:00', 'saved': False,
                 'counts': {**counts([]), 'open': 1, 'remembered': 6}},
                {'store_code': 'DT', 'date': '2026-11-01', 'parsed_at': '2026-11-03 19:55:00', 'saved': True,
                 'counts': {**counts([]), 'remembered': 14}},
            ]}
        if path == '/api/sales/name-picks/day':
            asked = parse_qs(query)
            return {'store_code': asked['store_code'][0], 'date': asked['date'][0],
                    'names': day_names(state) if asked['store_code'][0] == 'DT' and asked['date'][0] == '2026-11-02' else []}
        if path == '/api/sales/name-picks' and request.method == 'POST':
            body = request.post_data_json
            chosen = next((p for p in CATALOGUE if p['id'] == body['product_id']), None)
            state.setdefault('answered', {})[body.get('name_key') or body['raw_name'].casefold()] = chosen
            return {'ok': True}
        if path == '/api/sales/name-picks/ignore':
            body = request.post_data_json
            (state.setdefault('ignored', set()).add if body['ignored'] else state.setdefault('ignored', set()).discard)(body['name_key'])
            return {'ok': True}
        if path == '/api/sales/name-picks':  # DELETE
            return {'ok': True}
        if path == '/api/products/search':
            q = parse_qs(query).get('q', [''])[0].lower()
            found = [p for p in CATALOGUE if q in (p['jizhanming'] + ' ' + p['name_cn_en'] + ' ' + p['sku']).lower()]
            return found or [S1, S2]
        if path in ('/api/sales', '/api/products/section-aliases', '/api/sales/recorded-dates'):
            return []
        if path == '/api/sales/report-metadata':
            return {'cash_actual': None, 'cash_expected': None, 'cash_difference': None}
        if path == '/api/sales/parse_report':
            row = {'qty': 1, 'qty_pos': 1, 'qty_cash': 0, 'box_size': None, 'section': 'pos', 'flagged': False,
                   'note': '', 'unknown_header': None, 'warn_blank_jzm': False}
            return {'detected_date': '2026-11-02', 'store': 'DT', 'stock_mode': 'legacy', 'parser_engine': 'rules',
                    'confirmed': [{**row, 'raw_name': 'SA Laduree', 'product': LADUREE, 'score': 100,
                                   'pick': {'state': 'remembered', 'product_ids': [60], 'places': [place('pos')]}}],
                    'review': [
                        {**row, 'raw_name': 'smiski s1', 'qty': 2, 'qty_pos': 2, 'product': S1, 'score': 100,
                         'candidates': [{**S1, 'score': 100}, {**BALL1, 'score': 99}],
                         'pick': {'state': 'confirm', 'product_ids': [33], 'places': [place('cash')]}},
                        {**row, 'raw_name': 'SA特殊订单', 'product': SPECIAL_A, 'score': 99,
                         'candidates': [{**SPECIAL_A, 'score': 99}, {**SPECIAL_B, 'score': 99}],
                         'pick': {'state': 'disputed', 'product_ids': [3696, 3709], 'places': []}}],
                    'failed': [], 'unknown_sections': [], 'cash_total_reported': None, 'cash_expected_reported': None,
                    'multi_day': False, 'metadata_errors': []}
        if path == '/api/sales/submit_daily_report':
            return {'ok': True}
        return foundation_payload(path, query, False)
    return api_payload


async def open_page(browser, viewport, state, path='/sales/name-picks'):
    context, page = await context_with_api(browser, state, viewport=viewport, auth={'role': 'manager'})
    await context.add_init_script("localStorage.setItem('popcore_selected_store', JSON.stringify({id:1,code:'DT',name:'Downtown',color:'#6366f1'}))")
    await page.goto(BASE + path)
    return context, page


def posts(state, path, method='POST'):
    return [r['post_data'] for r in state['requests'] if r['path'] == path and r['method'] == method]


async def choose(page, scope, text, option):
    await scope.locator('.pc-pick-chooser').click()
    await scope.get_by_role('combobox').fill(text)
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option', has_text=option).first.click()


async def capture(page, name, height):
    """The shell scrolls inside its own pane, so a taller viewport is what fits the whole page in the shot."""
    size = page.viewport_size
    await expect(page.locator('.ant-message-notice')).to_have_count(0, timeout=8000)
    await page.set_viewport_size({'width': size['width'], 'height': height})
    await page.wait_for_timeout(300)
    await page.screenshot(path=OUT / name)
    await page.set_viewport_size(size)


async def no_sideways_scroll(page):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')


async def desktop_flow(browser):
    state = {}
    state['api_payload'] = api_for(state)
    context, page = await open_page(browser, {'width': 1440, 'height': 1000}, state)
    try:
        day = page.get_by_role('region', name='DT 2026-11-02 names')
        await expect(day).to_be_visible()
        await expect(day.get_by_text('11月2日 周一', exact=False).first).to_be_visible()
        # The report's own sections, in paste order.
        assert await day.locator('.pc-pick-section').evaluate_all('els => els.map(e => e.firstChild.textContent)') == [
            '卡机 POS', '随手记 Non-POS', '出库入店', '入店', '出店']
        # Each pasted day says how many names are still to pick and how many are remembered.
        await expect(page.get_by_role('button', name=re.compile('DT · 11月2日')).get_by_text('待选 6· 记住 1')).to_be_visible()
        s1 = page.locator('.pc-pick-row', has_text='smiski s1')
        await expect(s1.get_by_text('待选择', exact=True)).to_be_visible()
        await expect(s1.get_by_text('A guess: nothing is saved', exact=False)).to_be_visible()
        await capture(page, 'by-report-1440.png', 1500)

        # The matcher guessed the 球 strap; the owner's answer is Smiski Series 1.
        await choose(page, s1, 'Series 1', 'DR0033')
        await s1.get_by_role('button', name='记住 Remember').click()
        await expect(s1.get_by_text('已记住', exact=True)).to_be_visible()
        assert posts(state, '/api/sales/name-picks')[-1] == {
            'raw_name': 'smiski s1', 'note': '', 'product_id': 33, 'replaces': [],
            'store_code': 'DT', 'section': 'pos', 'display': False}
        # Remembered at DT 随手记; one tap confirms it for 卡机 too.
        laduree = page.locator('.pc-pick-row', has_text='SA Laduree')
        await expect(laduree.get_by_text('confirm it once for DT 卡机 POS', exact=False)).to_be_visible()
        await laduree.get_by_role('button', name='确认 Confirm').click()
        await expect(laduree.get_by_text('已记住', exact=True)).to_be_visible()
        assert posts(state, '/api/sales/name-picks')[-1]['replaces'] == [60]
        # A remembered product changed since: asked again, already picked.
        santa = page.locator('.pc-pick-row', has_text='Santa Claus').first
        await expect(santa.get_by_text('The product changed since it was picked', exact=False)).to_be_visible()
        await expect(santa.get_by_text('Display', exact=False).first).to_be_visible()
        # An outfit is not stock: set it aside.
        outfit = page.locator('.pc-pick-row', has_text='娃衣 恐龙')
        await outfit.get_by_role('button', name='忽略 Ignore').click()
        await expect(outfit.get_by_text('已忽略', exact=True)).to_be_visible()
        assert posts(state, '/api/sales/name-picks/ignore')[-1] == {'name_key': '娃衣 恐龙', 'note_key': '', 'ignored': True}
        # Changing a remembered name says what it replaces.
        dream = page.locator('.pc-pick-row', has_text='SA dream')
        await dream.get_by_role('button', name='改 Change').click()
        await choose(page, dream, 'Series 2', 'DR0034')
        await expect(dream.get_by_text('Replaces SA dream', exact=False)).to_be_visible()
        await expect(dream.get_by_role('button', name='替换 Replace')).to_be_visible()
        await dream.get_by_role('button', name='取消 Cancel').click()
        await no_sideways_scroll(page)

        await page.get_by_role('link', name=re.compile('记住的')).click()
        await expect(page.get_by_role('region', name='Remembered names')).to_be_visible()
        special = page.locator('.pc-pick-row', has_text='SA特殊订单')
        # Forget is a quiet button; only its confirmation is red.
        assert await page.locator('.pc-pick-row-all .ant-btn-dangerous').count() == 0
        add_boxes = await page.get_by_role('region', name='Add a name').locator('.ant-input, .pc-pick-chooser, .ant-btn').evaluate_all(
            'els => els.map(e => Math.round(e.getBoundingClientRect().bottom))')
        assert len(set(add_boxes)) == 1, add_boxes  # the form's fields and button share one bottom edge
        await expect(special.get_by_text('有冲突', exact=True)).to_be_visible()
        await expect(page.locator('.pc-pick-row', has_text='smiski hipper').get_by_text('Not confirmed on a paste yet', exact=False)).to_be_visible()
        await capture(page, 'all-picks-1440.png', 1250)
        # Add a name the owner answered in chat; it confirms nowhere until a paste asks once.
        add = page.get_by_role('region', name='Add a name')
        await add.get_by_placeholder('smiski s1').fill('SA kiss')
        await choose(page, add, 'kiss', 'DR0078')
        await add.get_by_role('button', name='记住 Remember').click()
        assert posts(state, '/api/sales/name-picks')[-1] == {'raw_name': 'SA kiss', 'note': '', 'product_id': 78, 'replaces': []}
        # Change a remembered pick: it replaces, by the key the page shows.
        dream = page.locator('.pc-pick-row', has_text='SA dream')
        await dream.get_by_role('button', name='改 Change').click()
        await choose(page, dream, 'Series 2', 'DR0034')
        await dream.get_by_role('button', name='替换 Replace').click()
        await expect(dream.get_by_role('button', name='改 Change')).to_be_visible()
        assert posts(state, '/api/sales/name-picks')[-1] == {
            'name_key': 'sa dream', 'note_key': '', 'raw_name': 'SA dream', 'note': '', 'product_id': 34, 'replaces': [90]}
        # Forget the disputed one-off (the owner: leave SA特殊订单 unmatched).
        await special.get_by_role('button', name='忘记 Forget').click()
        await page.get_by_role('tooltip').get_by_role('button', name='忘记 Forget').click()
        await expect(page.get_by_text('SA特殊订单: 忘记了 Forgotten', exact=False)).to_be_visible()
        assert posts(state, '/api/sales/name-picks', 'DELETE')[-1] == {'name_key': 'sa特殊订单', 'note_key': '', 'replaces': [3696, 3709]}
        await page.get_by_role('searchbox', name='Search remembered names').fill('laduree')
        await expect(page.locator('.pc-pick-row-all')).to_have_count(1)
        await no_sideways_scroll(page)
    except Exception:
        await page.screenshot(path=OUT / 'desktop-failure.png', full_page=True)
        raise
    finally:
        await context.close()


async def phone_flow(browser):
    state = {}
    state['api_payload'] = api_for(state)
    context, page = await open_page(browser, {'width': 390, 'height': 844}, state)
    try:
        # A phone opens on the list of pasted reports; a report opens on its own page.
        pastes = page.get_by_role('region', name='Pasted reports')
        await expect(pastes).to_be_visible()
        await expect(page.locator('.pc-pick-day')).to_have_count(0)
        await capture(page, 'reports-390.png', 844)
        await pastes.get_by_role('button', name=re.compile('DT · 11月2日')).click()
        await expect(page).to_have_url(re.compile(r'store=DT&date=2026-11-02'))
        await expect(page.get_by_role('region', name='DT 2026-11-02 names')).to_be_visible()
        heights = await page.locator('.pc-pick-actions .ant-btn').evaluate_all('els => els.map(e => e.getBoundingClientRect().height)')
        assert heights and min(heights) >= 44, heights
        await no_sideways_scroll(page)
        await capture(page, 'report-390.png', 2200)
        await page.goto(BASE + '/sales/name-picks?tab=all')
        await expect(page.get_by_role('region', name='Remembered names')).to_be_visible()
        await expect(page.get_by_role('link', name=re.compile('记住的.*· 5'))).to_be_visible()
        await no_sideways_scroll(page)
        await capture(page, 'all-picks-390.png', 2000)
    except Exception:
        await page.screenshot(path=OUT / 'phone-failure.png', full_page=True)
        raise
    finally:
        await context.close()


async def paste_review_flow(browser, viewport):
    state = {}
    state['api_payload'] = api_for(state)
    context, page = await open_page(browser, viewport, state, '/sales')
    try:
        await page.locator('textarea').fill('2026.11.2 DT\n卡机汇总：\nSA Laduree*1\nsmiski s1*2\nSA特殊订单*1')
        await page.get_by_role('button', name='Parse Report', exact=True).click()
        await expect(page.get_by_text('已记住 Remembered', exact=True)).to_be_visible()
        await expect(page.locator('.ant-alert').get_by_role('link', name='名称记忆 Name picks')).to_be_visible()
        await page.get_by_role('tab', name='Review', exact=False).click()
        review = page.get_by_role('tabpanel', name='Review', exact=False)
        await expect(review.get_by_text('first time here', exact=False)).to_be_visible()
        await expect(review.get_by_text('Your pick replaces both', exact=False)).to_be_visible()
        # The note is said where the manager decides: on a phone it is under the name, inside the screen.
        note = await review.get_by_text('first time here', exact=False).bounding_box()
        assert note['x'] >= 0 and note['x'] + note['width'] <= viewport['width'], note
        await capture(page, f'paste-review-{viewport["width"]}.png', 1600 if viewport['width'] > 400 else 2400)
        # Picking another product for a remembered name says it replaces the pick.
        row = review.locator('tr', has_text='smiski s1')
        await row.locator('.ant-select').first.click()
        await page.locator('.ant-select-dropdown:visible .ant-select-item-option', has_text='DR0038').first.click()
        await expect(row.get_by_text('Replaces the remembered Smiski Series 1', exact=False)).to_be_visible()
        # A remembered line can be changed: it goes back through review.
        await page.get_by_role('tab', name='Confirmed', exact=False).click()
        await page.get_by_role('button', name='Change SA Laduree').click()
        await page.get_by_role('tab', name='Review', exact=False).click()
        await expect(review.locator('tr', has_text='SA Laduree')).to_be_visible()
        await review.get_by_role('button', name='Accept all with match').click()
        await page.get_by_role('button', name='Confirm & Log', exact=False).click()
        await expect(page.get_by_text('Report imported', exact=False)).to_be_visible()
        items = {item['raw_name']: item for item in posts(state, '/api/sales/submit_daily_report')[-1]['items']}
        assert (items['smiski s1']['product_id'], items['smiski s1']['source_bucket']) == (38, 'review'), items
        assert (items['SA Laduree']['product_id'], items['SA Laduree']['source_bucket']) == (60, 'review'), items
        await no_sideways_scroll(page)
    except Exception:
        await page.screenshot(path=OUT / f'paste-review-{viewport["width"]}-failure.png', full_page=True)
        raise
    finally:
        await context.close()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'vite.log').open('w') as log:
        process = subprocess.Popen(['node', str(FRONTEND / 'node_modules/vite/bin/vite.js'),
                                    '--config', str(HERE / 'vite.config.mjs')], cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT)
        try:
            await wait_for_server(process)
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    await desktop_flow(browser)
                    await phone_flow(browser)
                    for size in ({'width': 1440, 'height': 1000}, {'width': 390, 'height': 844}):
                        await paste_review_flow(browser, size)
                finally:
                    await browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
    print('Name picks browser checks passed')


if __name__ == '__main__':
    asyncio.run(main())
