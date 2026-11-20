"""可疑人员 Watch list (owner, 2026-11-19: "upload images of stealers, so everyone can recognize the people that stole
from the store ... mark if it is resolved or not ... also something showing in the store computer"): managers add,
edit, resolve, reopen and delete records with photos; everyone signed in reads them, the viewer counter account
included; photos are private, re-encoded without EXIF, served only to a sign-in, and kept by backups."""
import io
from contextlib import closing
from pathlib import Path

from PIL import ExifTags, Image

from support import APP_DIR, IsolatedApiCase
import db
import watch_list
from blueprints import watch_list as watch_list_routes
from checkout_access import business_date


MANAGER = 'manager'
ADMIN = 'admin'


def photo_bytes(size=(40, 30), color='#3b82f6', fmt='PNG', **save):
    output = io.BytesIO()
    Image.new('RGB', size, color).save(output, format=fmt, **save)
    return output.getvalue()


def phone_photo():
    """A JPEG as a phone takes it: sideways pixels with Orientation 6, GPS and a description in EXIF."""
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    exif[ExifTags.Base.ImageDescription] = 'PRIVATE-DESCRIPTION'
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = 'N'
    gps[ExifTags.GPS.GPSLatitude] = (43.0, 39.0, 7.5)
    gps[ExifTags.GPS.GPSLongitudeRef] = 'W'
    gps[ExifTags.GPS.GPSLongitude] = (79.0, 23.0, 1.25)
    return photo_bytes((60, 20), '#ef4444', 'JPEG', exif=exif.tobytes(), comment=b'PRIVATE-COMMENT')


class WatchListCase(IsolatedApiCase):
    """DT and MK with two named managers, staff and the store computer's viewer account (check_watch_list.py builds
    on it)."""

    def setUp(self):
        super().setUp()
        self.app.register_blueprint(watch_list_routes.bp)
        with closing(self.connect()) as con:
            con.executemany('INSERT INTO employees(auth0_id, name) VALUES (?, ?)',
                            [('auth0|manager', 'Belle'), ('auth0|admin', 'Owner'), ('auth0|staff', 'Amy'),
                             ('auth0|counter', 'DT 柜台')])
            con.commit()

    def add(self, info='Tall, grey hoodie, took two Labubu boxes at 3 pm', store='DT', seen=None, photos=None,
            role=MANAGER):
        photos = [photo_bytes()] if photos is None else photos
        data = {'info': info, 'store_code': store, 'seen_on': seen or business_date(),
                'photos': [(io.BytesIO(content), f'photo{index}.png') for index, content in enumerate(photos)]}
        return self.client.post('/api/watch-list', data=data, content_type='multipart/form-data',
                                headers=self.headers(role))

    def added(self, **kwargs):
        response = self.add(**kwargs)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def listing(self, role='staff'):
        response = self.client.get('/api/watch-list', headers=self.headers(role))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def files(self):
        folder = watch_list.PHOTO_DIR
        return sorted(path.name for path in folder.iterdir()) if folder.exists() else []

    def counts(self):
        with closing(self.connect()) as con:
            return (con.execute('SELECT COUNT(*) FROM watch_list_entries').fetchone()[0],
                    con.execute('SELECT COUNT(*) FROM watch_list_photos').fetchone()[0])


class WatchListTests(WatchListCase):
    def test_a_manager_adds_a_record_with_photos(self):
        record = self.added(photos=[photo_bytes(), photo_bytes(color='#22c55e', fmt='WEBP')], seen='2026-11-18')
        self.assertEqual(record['info'], 'Tall, grey hoodie, took two Labubu boxes at 3 pm')
        self.assertEqual((record['store_code'], record['seen_on'], record['status']), ('DT', '2026-11-18', 'open'))
        self.assertEqual(record['created_by_name'], 'Belle')
        self.assertEqual(len(record['photos']), 2)
        self.assertEqual(len(self.files()), 2)
        self.assertTrue(all(name.endswith('.jpg') for name in self.files()))
        # Under the private upload root, never the static folder.
        self.assertNotIn(Path(APP_DIR / 'static').resolve(), watch_list.PHOTO_DIR.resolve().parents)
        # An admin adds too; the store may be left out.
        self.assertIsNone(self.added(role=ADMIN, store='')['store_code'])

    def test_staff_and_the_viewer_counter_account_list_and_see_the_photos(self):
        record = self.added()
        for role in ('staff', 'viewer:counter', 'viewer'):
            page = self.listing(role)
            self.assertFalse(page['can_edit'], role)
            self.assertEqual([item['id'] for item in page['records']], [record['id']])
            self.assertEqual([store['code'] for store in page['stores']], ['DT', 'MK'])
            response = self.client.get(f"/api/watch-list/photos/{record['photos'][0]['id']}", headers=self.headers(role))
            self.assertEqual(response.status_code, 200, role)
            self.assertEqual(response.mimetype, 'image/jpeg')
            self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            with Image.open(io.BytesIO(response.data)) as image:
                self.assertEqual(image.format, 'JPEG')
            response.close()
        self.assertTrue(self.listing(MANAGER)['can_edit'])

    def test_staff_and_viewers_cannot_write(self):
        record = self.added(photos=[photo_bytes(), photo_bytes(color='#000000')])
        entry, photo = record['id'], record['photos'][0]['id']
        before = (self.snapshot(['watch_list_entries', 'watch_list_photos']), self.files())
        def form(**fields):  # a fresh upload each time: a sent stream is used up
            return {'data': {**fields, 'photos': [(io.BytesIO(photo_bytes()), 'a.png')]},
                    'content_type': 'multipart/form-data'}
        writes = [
            ('post', '/api/watch-list', lambda: form(info='x', seen_on=business_date())),
            ('patch', f'/api/watch-list/{entry}', lambda: {'json': {'info': 'changed'}}),
            ('post', f'/api/watch-list/{entry}/photos', form),
            ('delete', f'/api/watch-list/{entry}/photos/{photo}', dict),
            ('post', f'/api/watch-list/{entry}/resolve', lambda: {'json': {'note': 'caught'}}),
            ('post', f'/api/watch-list/{entry}/reopen', dict),
            ('delete', f'/api/watch-list/{entry}', dict),
        ]
        for role in ('staff', 'viewer', 'viewer:counter'):
            for method, url, body in writes:
                response = getattr(self.client, method)(url, headers=self.headers(role), **body())
                self.assertEqual(response.status_code, 403, (role, method, url))
        self.assertEqual((self.snapshot(['watch_list_entries', 'watch_list_photos']), self.files()), before)

    def test_non_images_and_bad_fields_are_refused_and_nothing_is_kept(self):
        refused = {
            'text': b'just some text, not a photo',
            'fake png': b'\x89PNG\r\n\x1a\n' + b'0' * 64,
            'gif': photo_bytes(fmt='GIF'),
        }
        for name, content in refused.items():
            response = self.add(photos=[content])
            self.assertEqual(response.status_code, 400, name)
            self.assertEqual(response.get_json()['code'], 'invalid_image', name)
        # One bad photo among good ones refuses the whole record.
        self.assertEqual(self.add(photos=[photo_bytes(), b'not an image']).status_code, 400)
        with self.subTest('too large'):
            original = watch_list.MAX_IMAGE_BYTES
            watch_list.MAX_IMAGE_BYTES = 100
            try:
                response = self.add(photos=[photo_bytes((200, 200), fmt='BMP')])
            finally:
                watch_list.MAX_IMAGE_BYTES = original
            self.assertEqual(response.status_code, 413)
            self.assertEqual(response.get_json()['code'], 'image_too_large')
        self.assertEqual(self.add(photos=[]).get_json()['code'], 'photo_required')
        self.assertEqual(self.add(info='   ').status_code, 400)
        self.assertEqual(self.add(store='MT').status_code, 400)
        self.assertEqual(self.add(seen='2999-01-01').status_code, 400)
        self.assertEqual(self.add(seen='19 Nov').status_code, 400)
        self.assertEqual(self.counts(), (0, 0))
        self.assertEqual(self.files(), [])

    def test_photos_are_re_encoded_upright_without_exif(self):
        record = self.added(photos=[phone_photo()])
        self.assertEqual((record['photos'][0]['width'], record['photos'][0]['height']), (20, 60))
        response = self.client.get(f"/api/watch-list/photos/{record['photos'][0]['id']}", headers=self.headers('viewer'))
        content = response.data
        response.close()
        for marker in (b'PRIVATE-DESCRIPTION', b'PRIVATE-COMMENT', b'Exif'):
            self.assertNotIn(marker, content)
        with Image.open(io.BytesIO(content)) as image:
            self.assertEqual(image.size, (20, 60))
            self.assertEqual(len(image.getexif()), 0)
            self.assertNotIn('exif', image.info)
            self.assertNotIn('comment', image.info)
        # Big photos are shrunk to MAX_EDGE on their long side.
        big = self.added(photos=[photo_bytes((3000, 1000), fmt='JPEG')])['photos'][0]
        self.assertEqual((big['width'], big['height']), (watch_list.MAX_EDGE, 683))

    def test_photos_and_the_list_need_a_sign_in_with_a_role(self):
        photo = self.added()['photos'][0]['id']
        for headers in ({}, {'Authorization': 'Bearer'}):
            self.assertEqual(self.client.get(f'/api/watch-list/photos/{photo}', headers=headers).status_code, 401)
            self.assertEqual(self.client.get('/api/watch-list', headers=headers).status_code, 401)
        # An account no manager gave a role.
        self.assertEqual(self.client.get(f'/api/watch-list/photos/{photo}', headers=self.headers('norole:x')).status_code, 403)
        self.assertEqual(self.client.get('/api/watch-list', headers=self.headers('norole:x')).status_code, 403)
        self.assertEqual(self.client.get('/api/watch-list/photos/999', headers=self.headers('staff')).status_code, 404)

    def test_resolve_and_reopen(self):
        older = self.added(info='Older', seen='2026-11-01')
        newer = self.added(info='Newer', seen='2026-11-10')
        self.assertEqual([item['info'] for item in self.listing()['records']], ['Newer', 'Older'])

        response = self.client.post(f"/api/watch-list/{newer['id']}/resolve", json={'note': 'Police report 4471'},
                                    headers=self.headers(MANAGER))
        self.assertEqual(response.status_code, 200, response.get_json())
        resolved = response.get_json()
        self.assertEqual((resolved['status'], resolved['resolve_note'], resolved['resolved_by_name']),
                         ('resolved', 'Police report 4471', 'Belle'))
        self.assertTrue(resolved['resolved_at'])
        # Open records first, then resolved ones.
        self.assertEqual([(item['info'], item['status']) for item in self.listing()['records']],
                         [('Older', 'open'), ('Newer', 'resolved')])

        response = self.client.post(f"/api/watch-list/{newer['id']}/reopen", headers=self.headers(ADMIN))
        self.assertEqual(response.status_code, 200, response.get_json())
        reopened = response.get_json()
        self.assertEqual((reopened['status'], reopened['resolved_at'], reopened['resolved_by_name'], reopened['resolve_note']),
                         ('open', None, None, None))
        self.assertEqual(reopened['updated_by_name'], 'Owner')
        # Resolving without a note is fine.
        self.assertIsNone(self.client.post(f"/api/watch-list/{older['id']}/resolve",
                                           headers=self.headers(MANAGER)).get_json()['resolve_note'])

    def test_a_manager_edits_the_fields_and_the_photos(self):
        record = self.added(photos=[photo_bytes(), photo_bytes(color='#000000')])
        entry = record['id']
        response = self.client.patch(f'/api/watch-list/{entry}', headers=self.headers(MANAGER),
                                     json={'info': 'Red cap, took a Smiski', 'store_code': 'MK', 'seen_on': '2026-11-02'})
        self.assertEqual(response.status_code, 200, response.get_json())
        edited = response.get_json()
        self.assertEqual((edited['info'], edited['store_code'], edited['seen_on'], edited['updated_by_name']),
                         ('Red cap, took a Smiski', 'MK', '2026-11-02', 'Belle'))
        self.assertIsNone(self.client.patch(f'/api/watch-list/{entry}', headers=self.headers(MANAGER),
                                            json={'store_code': None}).get_json()['store_code'])
        self.assertEqual(self.client.patch(f'/api/watch-list/{entry}', headers=self.headers(MANAGER),
                                           json={'info': ''}).status_code, 400)

        added = self.client.post(f'/api/watch-list/{entry}/photos', headers=self.headers(MANAGER),
                                 data={'photos': [(io.BytesIO(photo_bytes(color='#facc15')), 'c.png')]},
                                 content_type='multipart/form-data')
        self.assertEqual(added.status_code, 201, added.get_json())
        photos = [photo['id'] for photo in added.get_json()['photos']]
        self.assertEqual(len(photos), 3)
        self.assertEqual(len(self.files()), 3)
        for photo in photos[:2]:
            response = self.client.delete(f'/api/watch-list/{entry}/photos/{photo}', headers=self.headers(MANAGER))
            self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(len(self.files()), 1)
        # A record keeps at least one photo.
        last = self.client.delete(f'/api/watch-list/{entry}/photos/{photos[2]}', headers=self.headers(MANAGER))
        self.assertEqual((last.status_code, last.get_json()['code']), (409, 'last_photo'))
        self.assertEqual(len(self.files()), 1)
        # Another record's photo is not this record's.
        other = self.added()
        self.assertEqual(self.client.delete(f"/api/watch-list/{entry}/photos/{other['photos'][0]['id']}",
                                            headers=self.headers(MANAGER)).status_code, 404)

    def test_at_most_max_photos(self):
        original = watch_list.MAX_PHOTOS
        watch_list.MAX_PHOTOS = 2
        try:
            self.assertEqual(self.add(photos=[photo_bytes()] * 3).status_code, 409)
            entry = self.added(photos=[photo_bytes()] * 2)['id']
            response = self.client.post(f'/api/watch-list/{entry}/photos', headers=self.headers(MANAGER),
                                        data={'photos': [(io.BytesIO(photo_bytes()), 'd.png')]},
                                        content_type='multipart/form-data')
            self.assertEqual((response.status_code, response.get_json()['code']), (409, 'too_many_photos'))
        finally:
            watch_list.MAX_PHOTOS = original
        self.assertEqual(len(self.files()), 2)

    def test_delete_removes_the_record_and_its_photo_files(self):
        kept = self.added(info='Kept')
        gone = self.added(info='Gone', photos=[photo_bytes(), photo_bytes(color='#000000')])
        self.assertEqual(len(self.files()), 3)
        response = self.client.delete(f"/api/watch-list/{gone['id']}", headers=self.headers(MANAGER))
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json(), {'deleted': gone['id'], 'photos_removed': 2})
        self.assertEqual(self.counts(), (1, 1))
        self.assertEqual(len(self.files()), 1)
        self.assertEqual([item['info'] for item in self.listing()['records']], ['Kept'])
        self.assertEqual(self.client.get(f"/api/watch-list/photos/{gone['photos'][0]['id']}",
                                         headers=self.headers('staff')).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/watch-list/{gone['id']}", headers=self.headers(MANAGER)).status_code, 404)
        response = self.client.get(f"/api/watch-list/photos/{kept['photos'][0]['id']}", headers=self.headers('staff'))
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_backups_keep_the_photos(self):
        from backup_package import create_package, restore_package
        record = self.added(photos=[photo_bytes(), photo_bytes(color='#000000')])
        roots = {'product': self.upload_dir, 'payment': self.upload_dir / 'payment_evidence',
                 'condition': self.upload_dir / 'condition_evidence', 'watch': watch_list.PHOTO_DIR}
        package, restored = Path(self.tempdir.name) / 'backup', Path(self.tempdir.name) / 'restored'
        manifest = create_package(db.DB_PATH, roots, package)
        self.assertEqual(sorted(item['path'] for item in manifest['attachments'] if item['kind'] == 'watch'), self.files())
        restore_package(package, restored)
        self.assertEqual(sorted(path.name for path in (restored / 'attachments' / 'watch').iterdir()), self.files())
        # A photo missing from the package is refused.
        (package / 'attachments' / 'watch' / self.files()[0]).unlink()
        with self.assertRaises(ValueError):
            restore_package(package, Path(self.tempdir.name) / 'restored-again')
        self.assertEqual(len(record['photos']), 2)


if __name__ == '__main__':
    import unittest
    unittest.main()
