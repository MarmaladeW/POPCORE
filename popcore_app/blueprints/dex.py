"""图鉴 Dex: the book in POPCORE (owner, 2026-11-12: "The Dex itself lives on POPCORE"). Any signed-in role
reads it; managers and admins write it. The rules are in dex_book.py and docs/dex-book.md.

Step 3, the Dex sync to popcore.ca (dex_sync.py): managers see its state and preview, retry refused entries and upload
pictures; only admins turn it on or off. Step 4: managers approve listings for stocked items with none."""
import io

from flask import Blueprint, jsonify, request
from PIL import Image, ImageSequence, UnidentifiedImageError

import dex_book
import dex_load
import dex_sync
from auth import login_required, role_required
from blueprints.products import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS
from db import get_db
from website_sync import WebsiteError

# The picture types the website takes (contract section 6), the same the app takes for product pictures.
PICTURE_TYPES = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp', 'AVIF': 'image/avif', 'GIF': 'image/gif'}

bp = Blueprint('dex', __name__)


def _run(work, status=200):
    con = get_db()
    try:
        return jsonify(work(con)), status
    except dex_book.DexError as exc:
        if con.in_transaction:
            con.rollback()
        return jsonify(error=str(exc), code=exc.code), exc.status


def _number(name):
    value = request.args.get(name, '')
    try:
        return int(value)
    except ValueError:
        raise dex_book.DexError(f'{name} must be a number.', 'invalid_input')


@bp.get('/api/dex/entries')
@login_required
def dex_list():
    def work(con):
        if 'parent' in request.args:
            return dex_book.children(con, _number('parent'))
        if 'named_ip' in request.args:  # contract 1.3: products and designs elsewhere that name this IP
            return dex_book.named(con, _number('named_ip'))
        if request.args.get('kind') == 'ip':  # every live IP, for the IP picker
            return dex_book.ips(con)
        if 'q' in request.args:
            kinds = [kind for kind in request.args.get('kinds', '').split(',') if kind]
            return dex_book.search(con, request.args.get('q'), kinds)
        return dex_book.top(con, request.args.get('kind'))
    return _run(work)


@bp.get('/api/dex/entries/<int:entry_id>')
@login_required
def dex_detail(entry_id):
    return _run(lambda con: dex_book.detail(con, entry_id))


@bp.get('/api/dex/entries/<int:entry_id>/removal')
@login_required
def dex_removal(entry_id):
    """What a delete would remove, for the confirm. Nothing changes."""
    def work(con):
        plan = dex_book.removal(con, entry_id)
        return {key: plan[key] for key in ('counts', 'linked', 'used_by')}
    return _run(work)


@bp.get('/api/dex/entries/<int:entry_id>/prices')
@login_required
def dex_prices(entry_id):
    """A design's or product's price trend: its POPCORE item's prices (design_prices for a confirmed design,
    product_price_history for any other item), and the website's market prices in its extras. Any signed-in
    role reads it; nothing changes."""
    return _run(lambda con: dex_book.price_history(con, entry_id))


@bp.get('/api/dex/unlinked')
@login_required
def dex_unlinked():
    """POPCORE items not in the book yet (no live book entry links them); never sealed sets. Each carries
    `suggestion` ({id, path, score}) or null: never linked automatically."""
    def work(con):
        page = _number('page') if request.args.get('page') else 1
        per_page = _number('per_page') if request.args.get('per_page') else 50
        found = dex_book.unlinked(con, request.args.get('q', ''), page, per_page)
        dex_load.suggestions_from_db(con, found['items'])  # the closest book entry by name, for a person to link
        dex_book.unlinked_names(con, found['items'])  # their full names and IP tags, beside
        return found
    return _run(work)


@bp.post('/api/dex/entries')
@role_required('manager')
def dex_create():
    return _run(lambda con: dex_book.create(con, request.get_json(silent=True), request.jwt_payload), 201)


@bp.patch('/api/dex/entries/<int:entry_id>')
@role_required('manager')
def dex_update(entry_id):
    return _run(lambda con: dex_book.update(con, entry_id, request.get_json(silent=True), request.jwt_payload))


@bp.delete('/api/dex/entries/<int:entry_id>')
@role_required('manager')
def dex_delete(entry_id):
    return _run(lambda con: dex_book.delete(con, entry_id, request.jwt_payload))


@bp.post('/api/dex/reorder')
@role_required('manager')
def dex_reorder():
    return _run(lambda con: dex_book.reorder(con, request.get_json(silent=True), request.jwt_payload))


# ── Step 3: the Dex sync to popcore.ca ───────────────────────────────────────

def _website_problem(exc):
    """A failed call to the website as a plain answer. The book route missing, or the key refused there, means the
    website's half of the sync is not there yet."""
    if exc.code in ('website_half_missing', 'website_auth'):
        message = dex_sync.HALF_MISSING if exc.code == 'website_half_missing' else str(exc)
        return jsonify(error=message, code='website_half_missing'), 409
    if exc.code == 'confirm_deletes':
        return jsonify(error=str(exc), code=exc.code), 400
    if exc.code == 'not_configured':
        return jsonify(error=str(exc), code=exc.code), 400
    return jsonify(error=str(exc), code=exc.code), 502


@bp.get('/api/dex/sync')
@role_required('manager')
def dex_sync_status():
    return jsonify(dex_sync.status(get_db()))


@bp.get('/api/dex/sync/preview')
@role_required('manager')
def dex_sync_preview():
    return jsonify(dex_sync.preview(get_db()))


@bp.get('/api/dex/sync/entries/<int:entry_id>')
@login_required
def dex_sync_entry(entry_id):
    """One entry's sync state for its page: sent, waiting, refused (with the website's reason) or behind."""
    return _run(lambda con: dex_sync.entry_state(con, entry_id))


@bp.post('/api/dex/sync/switch')
@role_required('admin')
def dex_sync_switch():
    """Turn the Dex sync on or off (admins). On first asks the website's book route; with deletes in the preview the
    request carries their count, typed by the admin (confirm_deletes)."""
    body = request.get_json(silent=True) or {}
    on = body.get('on')
    if not isinstance(on, bool):
        return jsonify(error='on is true or false.', code='invalid_input'), 400
    confirm = body.get('confirm_deletes')
    if isinstance(confirm, str) and confirm.strip().isdigit():
        confirm = int(confirm.strip())
    cfg, problem = dex_sync.settings()
    if on and cfg is None:
        return jsonify(error=problem, code='not_configured'), 400
    con = get_db()
    try:
        dex_sync.switch(con, cfg, on, request.jwt_payload.get('sub'), confirm if isinstance(confirm, int) else None)
    except WebsiteError as exc:
        if con.in_transaction:
            con.rollback()
        return _website_problem(exc)
    return jsonify(dex_sync.status(con))


@bp.post('/api/dex/sync/retry')
@role_required('manager')
def dex_sync_retry():
    """Send one refused entry ({id}) or every refused entry ({all: true}) again on the next pass."""
    body = request.get_json(silent=True) or {}
    entry_id = body.get('id')
    if body.get('all') is not True and (isinstance(entry_id, bool) or not isinstance(entry_id, int)):
        return jsonify(error='Give the entry’s id, or all.', code='invalid_input'), 400
    return _run(lambda con: {'retried': dex_sync.retry(con, None if body.get('all') is True else entry_id)})


# ── Step 4: listings on popcore.ca for stocked items with none (contract section 7) ──

def _item_id(body, field='product_id'):
    value = body.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise dex_book.DexError(f'Give the item’s {field}.', 'invalid_input')
    return value


@bp.get('/api/dex/listings')
@role_required('manager')
def dex_listings():
    """The catch-up preview: stocked items with no listing (ready, waiting for their book entry, or not in the book),
    the approved ones, the refused ones with the website's reason, and the answered ones. Nothing changes."""
    return _run(dex_sync.listings)


@bp.post('/api/dex/listings/approve')
@role_required('manager')
def dex_listings_approve():
    """{product_ids: [...]} or {all: true, count: n} (n: how many the screen showed). Approved items go to
    popcore.ca on the Dex sync's pass, only while its switch is on and once their book entry is on the website."""
    body = request.get_json(silent=True) or {}
    actor = request.jwt_payload.get('sub')

    def work(con):
        if body.get('all') is True:
            count = body.get('count')
            if isinstance(count, bool) or not isinstance(count, int):
                raise dex_book.DexError('Give the count the screen showed.', 'invalid_input')
            return dex_sync.approve_listings(con, None, actor, count)
        ids = body.get('product_ids')
        if not isinstance(ids, list) or not ids or len(ids) > dex_sync.LISTING_LIST or any(
                isinstance(one, bool) or not isinstance(one, int) for one in ids):
            raise dex_book.DexError('Give the items’ product_ids, or all with the count.', 'invalid_input')
        return dex_sync.approve_listings(con, list(dict.fromkeys(ids)), actor)
    return _run(work)


@bp.post('/api/dex/listings/withdraw')
@role_required('manager')
def dex_listings_withdraw():
    """{product_id}: an approved or refused item comes off the list and shows in the preview again."""
    body = request.get_json(silent=True) or {}
    return _run(lambda con: dex_sync.withdraw_listing(con, _item_id(body)))


@bp.post('/api/dex/listings/retry')
@role_required('manager')
def dex_listings_retry():
    """{product_id} or {all: true}: refused items go again with the next pass."""
    body = request.get_json(silent=True) or {}
    return _run(lambda con: dex_sync.retry_listings(con, None if body.get('all') is True else _item_id(body)))


@bp.post('/api/dex/entries/<int:entry_id>/picture')
@role_required('manager')
def dex_picture(entry_id):
    """A manager uploads a picture for an entry: POPCORE passes the file to the website's pictures route and keeps
    the id and addresses it returns (contract section 6). Same limits as product pictures."""
    if request.content_length and request.content_length > 12 * 1024 * 1024:
        return jsonify(error='That picture is too large (10 MB at most).', code='image_too_large'), 413
    upload = request.files.get('file')
    if upload is None:
        return jsonify(error='Choose a picture file.', code='invalid_input'), 400
    con = get_db()
    if con.execute('SELECT 1 FROM dex_entries WHERE id=? AND deleted_at IS NULL', (entry_id,)).fetchone() is None:
        return jsonify(error='That Dex entry does not exist.', code='dex_not_found'), 404
    content = upload.stream.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        return jsonify(error='That picture is too large (10 MB at most).', code='image_too_large'), 413
    try:
        with Image.open(io.BytesIO(content)) as image:
            image_format = image.format
            if image.width * image.height * getattr(image, 'n_frames', 1) > MAX_IMAGE_PIXELS:
                return jsonify(error='That picture has too many pixels.', code='image_too_large'), 413
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            for frame in ImageSequence.Iterator(image):
                frame.load()
        mimetype = PICTURE_TYPES[image_format]
    except (KeyError, OSError, SyntaxError, UnidentifiedImageError, ValueError):
        return jsonify(error='That is not a JPEG, PNG, WebP, AVIF or GIF picture.', code='invalid_image'), 400
    cfg, problem = dex_sync.settings()
    if cfg is None:
        return jsonify(error=problem, code='not_configured'), 400
    try:
        picture_id, url, thumb = dex_sync.upload_picture(cfg, entry_id, content, upload.filename or 'picture', mimetype)
    except WebsiteError as exc:
        return _website_problem(exc)
    return _run(lambda con: dex_book.set_picture(con, entry_id, picture_id, url, thumb, request.jwt_payload))
