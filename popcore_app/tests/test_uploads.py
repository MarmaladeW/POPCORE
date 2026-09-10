import io
import sqlite3
import sys
from pathlib import Path
from contextlib import closing

from PIL import Image

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from support import IsolatedApiCase


def png_1x1():
    output = io.BytesIO()
    Image.new('RGB', (1, 1), 'white').save(output, format='PNG')
    return output.getvalue()


def animated_gif():
    output = io.BytesIO()
    frames = [Image.new('RGB', (2, 2), color) for color in ('white', 'black')]
    frames[0].save(
        output, format='GIF', save_all=True, append_images=frames[1:], duration=10
    )
    return output.getvalue()


class ProductImageUploadTests(IsolatedApiCase):
    def upload(self, product_id, content, filename):
        response = self.client.post(
            f'/api/products/{product_id}/hidden_images',
            headers=self.headers('manager'),
            data={'image': (io.BytesIO(content), filename)},
            content_type='multipart/form-data',
        )
        response.request.environ['wsgi.input'].close()
        self.addCleanup(response.close)
        return response

    def test_fake_jpeg_is_rejected_without_file_or_row(self):
        response = self.upload(self.product_id, b'not an image', 'fake.jpg')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'invalid_image')
        self.assertFalse(self.upload_dir.exists())
        self.assertEqual(self.snapshot(('hidden_images',))['hidden_images'], [])

    def test_valid_png_is_stored_with_detected_extension(self):
        response = self.upload(self.product_id, png_1x1(), 'phone-upload.jpg')

        self.assertEqual(response.status_code, 201)
        saved = list(self.upload_dir.rglob('*'))
        files = [path for path in saved if path.is_file()]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].suffix, '.png')

    def test_valid_animated_gif_is_fully_accepted(self):
        response = self.upload(self.product_id, animated_gif(), 'animated.gif')

        self.assertEqual(response.status_code, 201)
        files = [path for path in self.upload_dir.rglob('*') if path.is_file()]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].suffix, '.gif')

    def test_missing_product_does_not_create_directory(self):
        response = self.upload(999999, png_1x1(), 'valid.png')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['code'], 'not_found')
        self.assertFalse(self.upload_dir.exists())

    def test_oversized_file_is_rejected_before_persisting(self):
        response = self.upload(
            self.product_id, b'x' * (10 * 1024 * 1024 + 1), 'huge.jpg'
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()['code'], 'image_too_large')
        self.assertFalse(self.upload_dir.exists())

    def test_truncated_png_is_rejected(self):
        response = self.upload(self.product_id, png_1x1()[:-8], 'broken.png')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'invalid_image')
        self.assertFalse(self.upload_dir.exists())

    def test_excessive_dimensions_are_rejected(self):
        import blueprints.products as products

        original_limit = products.MAX_IMAGE_PIXELS
        products.MAX_IMAGE_PIXELS = 0
        try:
            response = self.upload(self.product_id, png_1x1(), 'large.png')
        finally:
            products.MAX_IMAGE_PIXELS = original_limit

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()['code'], 'image_too_large')
        self.assertFalse(self.upload_dir.exists())

    def test_database_failure_removes_only_the_new_file(self):
        with closing(self.connect()) as con:
            con.execute(
                """CREATE TRIGGER reject_hidden_image
                   BEFORE INSERT ON hidden_images
                   BEGIN SELECT RAISE(ABORT, 'fixture failure'); END"""
            )
            con.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.upload(self.product_id, png_1x1(), 'valid.png')

        files = list(self.upload_dir.rglob('*')) if self.upload_dir.exists() else []
        self.assertFalse(any(path.is_file() for path in files))
        self.assertEqual(self.snapshot(('hidden_images',))['hidden_images'], [])
