"""可疑人员 Watch list: photos and notes of people who stole from the stores.

Owner, 2026-11-19: "in the 损耗 section, there can be a section where I can upload images of stealers, so everyone can
recognize the people that stole from the store, we can write own information about the stealer, and upload image of
them, and we can mark if it is resolved or not, the stealer information also something showing in the store computer."
Managers and admins add and edit; everyone signed in with a role reads, the store computer's viewer account included.

Photos are private files under the upload root, beside payment and condition evidence, and leave only through the
signed-in photo route. Each upload is decoded with Pillow, turned upright, shrunk to at most MAX_EDGE pixels and saved
again as a fresh JPEG, so nothing of the original file (EXIF, GPS, comments) is kept.
"""
import io
import os
import re
import sqlite3
import uuid
from datetime import date
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

import payment_evidence
from checkout_access import business_date


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000      # a 48-megapixel phone photo still fits
MAX_EDGE = 2048
MAX_PHOTOS = 12
MAX_INFO = 2000
MAX_NOTE = 500
FORMATS = {'JPEG', 'PNG', 'WEBP'}
STORES = ('DT', 'MK')              # the store a person was seen at is DT, MK or not given
# Beside payment_evidence/ under the same private upload root unless set: popcore_app/uploads/watch_list by default,
# /var/lib/popcore/uploads/watch_list where the systemd template moves the uploads.
PHOTO_DIR = Path(os.environ.get('POPCORE_WATCH_LIST_DIR', payment_evidence.EVIDENCE_DIR.parent / 'watch_list'))
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')


class WatchListError(Exception):
    def __init__(self, message, code='invalid_input', status=400):
        super().__init__(message)
        self.code = code
        self.status = status


# ─── Photos ───────────────────────────────────────────────────────────────────

def prepare_photo(upload):
    """A checked, re-encoded JPEG from one uploaded file, or WatchListError. Nothing is written yet."""
    if upload is None:
        raise WatchListError('A photo is required', 'invalid_image')
    content = upload.stream.read(MAX_IMAGE_BYTES + 1)
    if not content:
        raise WatchListError('A photo is empty', 'invalid_image')
    if len(content) > MAX_IMAGE_BYTES:
        raise WatchListError('Each photo must be 10 MB or smaller', 'image_too_large', 413)
    try:
        with Image.open(io.BytesIO(content)) as probe:
            if probe.format not in FORMATS or getattr(probe, 'n_frames', 1) != 1:
                raise WatchListError('Only still JPEG, PNG or WebP photos can be added', 'invalid_image')
            if probe.width * probe.height > MAX_IMAGE_PIXELS:
                raise WatchListError('The photo has too many pixels', 'image_too_large', 413)
            probe.verify()
        with Image.open(io.BytesIO(content)) as image:
            if image.format == 'JPEG':
                image.draft('RGB', (MAX_EDGE, MAX_EDGE))  # decode big JPEGs at a smaller scale
            image.load()
            upright = ImageOps.exif_transpose(image)       # phones say which way is up only in EXIF
            upright.thumbnail((MAX_EDGE, MAX_EDGE))
            rgba = upright.convert('RGBA')
            # A fresh image carries none of the original's metadata.
            clean = Image.new('RGB', rgba.size, 'white')
            clean.paste(rgba, mask=rgba.getchannel('A'))
            output = io.BytesIO()
            clean.save(output, format='JPEG', quality=85, optimize=True)
    except WatchListError:
        raise
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError, Image.DecompressionBombError) as exc:
        raise WatchListError('The file is not a photo that can be read', 'invalid_image') from exc
    data = output.getvalue()
    return {'object_id': f'{uuid.uuid4().hex}.jpg', 'mime_type': 'image/jpeg', 'content': data,
            'byte_size': len(data), 'width': clean.width, 'height': clean.height}


def photo_path(object_id):
    if Path(object_id).name != object_id:
        raise WatchListError('Photo is invalid', 'not_found', 404)
    return PHOTO_DIR / object_id


def _publish(prepared):
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    final_path = PHOTO_DIR / prepared['object_id']
    part_path = final_path.with_suffix(final_path.suffix + '.part')
    with part_path.open('xb') as output:
        output.write(prepared['content'])
    os.replace(part_path, final_path)
    return final_path


def _unlink(object_ids):
    for object_id in object_ids:
        try:
            photo_path(object_id).unlink()
        except (FileNotFoundError, WatchListError):
            pass


def _prepare_all(uploads):
    uploads = [upload for upload in uploads or [] if upload is not None and upload.filename]  # an empty file field
    return [prepare_photo(upload) for upload in uploads]


def _store_photos(con, entry_id, prepared, actor):
    """Writes the files, then their rows; a failed write removes this call's files again."""
    written = []
    try:
        for photo in prepared:
            _publish(photo)
            written.append(photo['object_id'])
            con.execute("""INSERT INTO watch_list_photos(entry_id, object_id, mime_type, byte_size, width, height, uploaded_by)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (entry_id, photo['object_id'], photo['mime_type'], photo['byte_size'], photo['width'],
                         photo['height'], actor['sub']))
    except (OSError, sqlite3.Error):
        if con.in_transaction:
            con.rollback()
        _unlink(written)
        raise


# ─── Records ──────────────────────────────────────────────────────────────────

_SELECT = """SELECT w.*, s.code AS store_code, s.name AS store_name,
                    NULLIF(c.name, '') AS created_by_name, NULLIF(u.name, '') AS updated_by_name,
                    NULLIF(r.name, '') AS resolved_by_name
             FROM watch_list_entries w
             LEFT JOIN stores s ON s.id = w.store_id
             LEFT JOIN employees c ON c.auth0_id = w.created_by
             LEFT JOIN employees u ON u.auth0_id = w.updated_by
             LEFT JOIN employees r ON r.auth0_id = w.resolved_by"""


def _records(con, where='', params=()):
    rows = con.execute(f"""{_SELECT} {where}
        ORDER BY w.status = 'resolved', w.seen_on DESC, w.id DESC""", params).fetchall()
    photos = {}
    for photo in con.execute('SELECT id, entry_id, width, height FROM watch_list_photos ORDER BY entry_id, id'):
        photos.setdefault(photo['entry_id'], []).append({'id': photo['id'], 'width': photo['width'],
                                                         'height': photo['height']})
    return [{
        'id': row['id'], 'info': row['info'], 'store_code': row['store_code'], 'store_name': row['store_name'],
        'seen_on': row['seen_on'], 'status': row['status'],
        'resolved_at': row['resolved_at'], 'resolved_by_name': row['resolved_by_name'], 'resolve_note': row['resolve_note'],
        'created_at': row['created_at'], 'created_by_name': row['created_by_name'],
        'updated_at': row['updated_at'], 'updated_by_name': row['updated_by_name'],
        'photos': photos.get(row['id'], []),
    } for row in rows]


def _record(con, entry_id):
    found = _records(con, 'WHERE w.id = ?', (entry_id,))
    if not found:
        raise WatchListError('This record was not found; it may have been deleted', 'not_found', 404)
    return found[0]


def list_page(con, can_edit):
    """Open records first, newest seen first, then the resolved ones."""
    stores = [dict(row) for row in con.execute(
        f"SELECT code, name FROM stores WHERE is_active = 1 AND code IN ({','.join('?' * len(STORES))}) ORDER BY id",
        STORES)]
    return {'records': _records(con), 'stores': stores, 'today': business_date(), 'can_edit': bool(can_edit),
            'max_photos': MAX_PHOTOS}


def _fields(con, data, partial=False):
    """The record's own fields from a form or JSON body: info, store_code (optional) and seen_on."""
    fields = {}
    if not partial or 'info' in data:
        info = str(data.get('info') or '').strip()
        if not info:
            raise WatchListError('Write what the person looks like, what was taken and when')
        if len(info) > MAX_INFO:
            raise WatchListError(f'The information is longer than {MAX_INFO} characters')
        fields['info'] = info
    if not partial or 'store_code' in data:
        code = str(data.get('store_code') or '').strip().upper()
        if not code:
            fields['store_id'] = None
        else:
            row = con.execute(f"SELECT id FROM stores WHERE code = ? AND is_active = 1 AND code IN ({','.join('?' * len(STORES))})",
                              (code, *STORES)).fetchone()
            if row is None:
                raise WatchListError('The store must be DT, MK or left empty')
            fields['store_id'] = row['id']
    if not partial or 'seen_on' in data:
        seen = str(data.get('seen_on') or '').strip()
        if not _DATE.fullmatch(seen):
            raise WatchListError('The date seen must be a date like 2026-11-19')
        try:
            date.fromisoformat(seen)
        except ValueError as exc:
            raise WatchListError('The date seen must be a date like 2026-11-19') from exc
        if seen > business_date():
            raise WatchListError('The date seen cannot be after today')
        fields['seen_on'] = seen
    return fields


def create(con, actor, data, uploads):
    fields = _fields(con, data)
    prepared = _prepare_all(uploads)
    if not prepared:
        raise WatchListError('Add at least one photo', 'photo_required')
    if len(prepared) > MAX_PHOTOS:
        raise WatchListError(f'A record holds at most {MAX_PHOTOS} photos', 'too_many_photos', 409)
    entry_id = con.execute("""INSERT INTO watch_list_entries(info, store_id, seen_on, created_by)
                              VALUES (?, ?, ?, ?)""",
                           (fields['info'], fields['store_id'], fields['seen_on'], actor['sub'])).lastrowid
    _store_photos(con, entry_id, prepared, actor)
    con.commit()
    return _record(con, entry_id)


def _existing(con, entry_id):
    row = con.execute('SELECT * FROM watch_list_entries WHERE id = ?', (entry_id,)).fetchone()
    if row is None:
        raise WatchListError('This record was not found; it may have been deleted', 'not_found', 404)
    return row


def update(con, actor, entry_id, data):
    _existing(con, entry_id)
    fields = _fields(con, data if isinstance(data, dict) else {}, partial=True)
    if not fields:
        raise WatchListError('Nothing to change')
    assignments = ', '.join(f'{name} = ?' for name in fields)
    con.execute(f"UPDATE watch_list_entries SET {assignments}, updated_by = ?, updated_at = datetime('now') WHERE id = ?",
                (*fields.values(), actor['sub'], entry_id))
    con.commit()
    return _record(con, entry_id)


def add_photos(con, actor, entry_id, uploads):
    _existing(con, entry_id)
    prepared = _prepare_all(uploads)
    if not prepared:
        raise WatchListError('Choose a photo to add', 'photo_required')
    count = con.execute('SELECT COUNT(*) FROM watch_list_photos WHERE entry_id = ?', (entry_id,)).fetchone()[0]
    if count + len(prepared) > MAX_PHOTOS:
        raise WatchListError(f'A record holds at most {MAX_PHOTOS} photos', 'too_many_photos', 409)
    _store_photos(con, entry_id, prepared, actor)
    con.execute("UPDATE watch_list_entries SET updated_by = ?, updated_at = datetime('now') WHERE id = ?",
                (actor['sub'], entry_id))
    con.commit()
    return _record(con, entry_id)


def remove_photo(con, actor, entry_id, photo_id):
    _existing(con, entry_id)
    row = con.execute('SELECT object_id FROM watch_list_photos WHERE id = ? AND entry_id = ?',
                      (photo_id, entry_id)).fetchone()
    if row is None:
        raise WatchListError('This photo was not found; it may have been removed', 'not_found', 404)
    if con.execute('SELECT COUNT(*) FROM watch_list_photos WHERE entry_id = ?', (entry_id,)).fetchone()[0] <= 1:
        raise WatchListError('A record keeps at least one photo. Add the new photo first, or delete the record',
                             'last_photo', 409)
    con.execute('DELETE FROM watch_list_photos WHERE id = ?', (photo_id,))
    con.execute("UPDATE watch_list_entries SET updated_by = ?, updated_at = datetime('now') WHERE id = ?",
                (actor['sub'], entry_id))
    con.commit()
    _unlink([row['object_id']])
    return _record(con, entry_id)


def resolve(con, actor, entry_id, data):
    _existing(con, entry_id)
    note = str((data or {}).get('note') or '').strip() if isinstance(data, dict) else ''
    if len(note) > MAX_NOTE:
        raise WatchListError(f'The note is longer than {MAX_NOTE} characters')
    con.execute("""UPDATE watch_list_entries SET status = 'resolved', resolved_at = datetime('now'), resolved_by = ?,
                   resolve_note = ? WHERE id = ?""", (actor['sub'], note or None, entry_id))
    con.commit()
    return _record(con, entry_id)


def reopen(con, actor, entry_id):
    _existing(con, entry_id)
    con.execute("""UPDATE watch_list_entries SET status = 'open', resolved_at = NULL, resolved_by = NULL,
                   resolve_note = NULL, updated_by = ?, updated_at = datetime('now') WHERE id = ?""",
                (actor['sub'], entry_id))
    con.commit()
    return _record(con, entry_id)


def delete(con, entry_id):
    """The record, its photo rows and then its photo files."""
    _existing(con, entry_id)
    object_ids = [row['object_id'] for row in
                  con.execute('SELECT object_id FROM watch_list_photos WHERE entry_id = ?', (entry_id,))]
    con.execute('DELETE FROM watch_list_photos WHERE entry_id = ?', (entry_id,))
    con.execute('DELETE FROM watch_list_entries WHERE id = ?', (entry_id,))
    con.commit()
    _unlink(object_ids)
    return {'deleted': entry_id, 'photos_removed': len(object_ids)}


def photo_file(con, photo_id):
    row = con.execute('SELECT object_id, mime_type FROM watch_list_photos WHERE id = ?', (photo_id,)).fetchone()
    if row is None:
        raise WatchListError('This photo was not found', 'not_found', 404)
    path = photo_path(row['object_id'])
    if not path.is_file():
        raise WatchListError('This photo file is missing', 'photo_missing', 404)
    return path, row['mime_type']
