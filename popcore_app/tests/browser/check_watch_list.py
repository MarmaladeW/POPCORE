"""可疑人员 Watch list in a real browser against the real Flask routes and a disposable database (owner, 2026-11-19:
"upload images of stealers, so everyone can recognize the people that stole from the store ... mark if it is resolved
or not ... also something showing in the store computer"): a manager opens it from 损耗, adds a record with two photos,
opens a photo large, edits it (a photo out, another in), resolves it into the folded section, reopens it and deletes a
record; staff and the store computer's viewer account see the records and their photos with no edit controls; photos
are blob URLs from the signed-in API; nothing scrolls sideways, at 390 and 1440 px."""
import asyncio
import io
import re
import subprocess
import sys
from urllib.parse import urlsplit

from PIL import Image, ImageDraw
from playwright.async_api import async_playwright, expect
from check_foundation import FRONTEND, HERE, ROOT

PORT = 5197
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / '.local' / 'watch-list'


def picture(color, name):
    """A stand-in photo: a figure on a plain background, 600 × 800."""
    image = Image.new('RGB', (600, 800), '#d9dde3')
    draw = ImageDraw.Draw(image)
    draw.ellipse((220, 120, 380, 280), fill='#8a6f5a')
    draw.rectangle((170, 300, 430, 760), fill=color)
    output = io.BytesIO()
    image.save(output, format='JPEG', quality=80)
    return {'name': name, 'mimeType': 'image/jpeg', 'buffer': output.getvalue()}


async def fits(page, name):
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    await page.screenshot(path=OUT / name, animations='disabled', full_page=True)


async def run(browser, width):
    sys.path.insert(0, str(ROOT / 'popcore_app/tests'))
    from test_watch_list import WatchListCase, photo_bytes
    import watch_list
    fixture = WatchListCase()
    fixture.setUp()
    contexts, refused = [], []

    async def open_as(role, token=None):
        token = token or role  # the server's sign-in: 'viewer:counter' is the viewer auth0|counter
        context = await browser.new_context(viewport={'width': width, 'height': 900})
        contexts.append(context)
        await context.add_init_script(
            f"window.__FOUNDATION_AUTH={{role:'{role}',token:'{role}'}};"
            f"localStorage.setItem('popcore_selected_store',JSON.stringify({{id:{fixture.store_id},code:'DT',name:'Downtown Toronto'}}));")
        page = await context.new_page()

        async def flask_boundary(route):
            parsed = urlsplit(route.request.url)
            if parsed.hostname not in ('127.0.0.1', 'localhost'):
                await route.abort(); return
            if not parsed.path.startswith('/api/'):
                await route.continue_(); return
            kind = route.request.headers.get('content-type', '')
            body = {}
            if kind.startswith('multipart/form-data'):
                body = {'data': route.request.post_data_buffer, 'content_type': kind}
            elif route.request.post_data:
                body = {'json': route.request.post_data_json}
            response = fixture.client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                           method=route.request.method,
                                           headers={'Authorization': f'Bearer {token}'}, **body)
            if parsed.path.startswith('/api/watch-list') and response.status_code >= 400:
                refused.append((role, route.request.method, parsed.path, response.status_code))
            data = response.get_data()
            response.close()
            await route.fulfill(status=response.status_code, body=data, content_type=response.content_type)
        await page.route('**/*', flask_boundary)
        return page

    try:
        # A manager finds it from 损耗.
        page = await open_as('manager')
        await page.goto(BASE + '/stock/loss')
        link = page.locator('a.pc-loss-watch')  # the rail has its own 可疑人员 link on desktop
        await expect(link).to_contain_text('0 未解决 open')
        await fits(page, f'loss-link-{width}.png')
        await link.click()
        await expect(page).to_have_url(BASE + '/watch-list')
        await expect(page.get_by_text('Only for recognising people in the stores; do not share outside POPCORE.')).to_be_visible()
        await expect(page.get_by_text('没有未解决的记录')).to_be_visible()

        # Add a record with two photos.
        await page.get_by_role('button', name=re.compile('添加.*Add a record')).click()
        form = page.get_by_role('region', name='添加记录 Add a record')
        await form.get_by_label('添加照片 Add photos').set_input_files([picture('#3f5f8a', 'front.jpg'), picture('#5a3f3f', 'side.jpg')])
        await expect(form.get_by_role('img', name=re.compile('^新照片'))).to_have_count(2)
        await form.get_by_label('情况 Information').fill('Tall, grey hoodie, black cap. Took two Labubu boxes from the front table around 3 pm.')
        await form.get_by_role('button', name='DT Downtown Toronto').click()
        await fits(page, f'add-{width}.png')
        await form.get_by_role('button', name='添加 Add', exact=True).click()
        await expect(page.get_by_text('已添加 Added')).to_be_visible()
        open_panel = page.get_by_role('region', name='未解决 Open')
        card = open_panel.get_by_role('article')
        await expect(card).to_have_count(1)
        await expect(card.get_by_text('Took two Labubu boxes', exact=False)).to_be_visible()
        await expect(card.get_by_text('DT Downtown Toronto')).to_be_visible()
        await expect(card.get_by_text('Belle', exact=False)).to_be_visible()
        photos = card.locator('.pc-watch-photo img')
        await expect(photos).to_have_count(2)
        await expect(photos.first).to_have_attribute('src', re.compile('^blob:'))
        with fixture.connect() as con:
            assert con.execute('SELECT COUNT(*) FROM watch_list_photos').fetchone()[0] == 2
        await fits(page, f'manager-{width}.png')

        # A photo opens large.
        await card.locator('.pc-watch-photo').first.click()  # the thumbnail, under its 放大 View mask
        await expect(page.locator('.ant-image-preview-img')).to_be_visible()
        await page.screenshot(path=OUT / f'photo-large-{width}.png', animations='disabled')
        await page.keyboard.press('Escape')
        await expect(page.locator('.ant-image-preview-img')).to_be_hidden()

        # Edit: new words, one photo out, another in.
        await card.get_by_role('button', name=re.compile('^编辑')).click()
        edit = page.get_by_role('region', name='编辑记录 Edit record')
        await edit.get_by_label('情况 Information').fill('Tall, grey hoodie, black cap. Took two Labubu boxes. Came back on Nov 18.')
        await edit.get_by_role('button', name='移除 Remove').first.click()
        await edit.get_by_label('添加照片 Add photos').set_input_files([picture('#2f6b4c', 'back.jpg')])
        await edit.get_by_role('button', name='保存 Save').click()
        await expect(page.get_by_text('已保存 Saved')).to_be_visible()
        await expect(card.get_by_text('Came back on Nov 18.', exact=False)).to_be_visible()
        await expect(card.get_by_text('修改 Edited', exact=False)).to_be_visible()
        await expect(card.locator('.pc-watch-photo img')).to_have_count(2)

        # Resolve it with a note: it leaves 未解决 for the folded 已解决.
        await card.get_by_role('button', name=re.compile('^标记已解决')).click()
        await card.get_by_label('怎么解决的 How it was resolved').fill('Paid for the boxes on Nov 19')
        await card.get_by_role('button', name=re.compile('^确认已解决')).click()
        await expect(page.get_by_text('已解决 Marked resolved')).to_be_visible()
        await expect(open_panel.get_by_role('article')).to_have_count(0)
        resolved = page.locator('details.pc-watch-resolved')
        await expect(resolved.get_by_role('article')).to_be_hidden()
        await resolved.locator('summary').click()
        await expect(resolved.get_by_text('Paid for the boxes on Nov 19')).to_be_visible()
        await fits(page, f'resolved-{width}.png')
        # And back again.
        await resolved.get_by_role('button', name=re.compile('^重新打开')).click()
        await page.locator('.ant-popconfirm').get_by_role('button', name='重新打开 Reopen').click()
        await expect(page.get_by_text('已重新打开 Reopened')).to_be_visible()
        await expect(open_panel.get_by_role('article')).to_have_count(1)

        # Delete a record: its photo files go too.
        response = fixture.add(info='Short, red jacket, took a Smiski', store='MK', seen='2026-11-02')
        assert response.status_code == 201, response.get_json()
        gone = response.get_json()
        files_before = len(fixture.files())
        await page.reload()
        target = page.get_by_role('article', name=re.compile('2026-11-02 · MK'))
        await target.get_by_role('button', name=re.compile('^删除')).click()
        await page.locator('.ant-popconfirm').get_by_role('button', name='删除 Delete').click()
        await expect(page.get_by_text('已删除 Deleted')).to_be_visible()
        await expect(target).to_have_count(0)
        assert len(fixture.files()) == files_before - len(gone['photos'])

        # Staff and the store computer read it, photos included, with nothing to change.
        resolved_one = fixture.added(info='Older case, paid in the end', seen='2026-10-30', photos=[photo_bytes((300, 400))])
        fixture.client.post(f"/api/watch-list/{resolved_one['id']}/resolve", json={'note': 'Paid'}, headers=fixture.headers('manager'))
        for role, token, shot in (('staff', 'staff', 'staff'), ('viewer', 'viewer:counter', 'counter')):
            page = await open_as(role, token)
            await page.goto(BASE + '/watch-list')
            open_panel = page.get_by_role('region', name='未解决 Open')
            await expect(open_panel.get_by_role('article')).to_have_count(1)
            await expect(open_panel.locator('.pc-watch-photo img')).to_have_count(2)
            await expect(open_panel.locator('.pc-watch-photo img').first).to_have_attribute('src', re.compile('^blob:'))
            await expect(page.locator('details.pc-watch-resolved summary')).to_contain_text('已解决')
            for name in ('添加', '编辑', '删除', '标记已解决', '重新打开'):
                await expect(page.get_by_role('button', name=re.compile(f'^{name}'))).to_have_count(0)
            if width > 600:
                rail = page.get_by_role('navigation', name='Primary navigation')
                await expect(rail.get_by_role('link', name='可疑人员 Watch list')).to_have_attribute('aria-current', 'page')
            await open_panel.locator('.pc-watch-photo').first.click()
            await expect(page.locator('.ant-image-preview-img')).to_be_visible()
            await page.keyboard.press('Escape')
            await expect(page.locator('.ant-image-preview-img')).to_be_hidden()
            await page.locator('details.pc-watch-resolved summary').click()
            await expect(page.get_by_text('Older case, paid in the end')).to_be_visible()
            await fits(page, f'{shot}-{width}.png')
        assert not [item for item in refused if item[3] != 404], refused
        assert watch_list.PHOTO_DIR.resolve().is_relative_to((ROOT / '.local').resolve()), watch_list.PHOTO_DIR
    finally:
        for context in contexts:
            await context.close()
        fixture.tearDown()
        fixture.doCleanups()


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
    print('PASS: 可疑人员 Watch list from 损耗, adding a record with photos, a photo opened large, editing words and '
          'photos, resolving into the folded section and reopening, deleting with its files, staff and the viewer '
          'counter account reading with photos and no edit controls, at 390 and 1440 px')
