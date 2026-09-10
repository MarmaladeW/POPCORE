import io
import shutil
import sqlite3
from contextlib import closing

from PIL import Image

from test_payments import PaymentTests


class PaymentEvidenceTests(PaymentTests):
    def payment(self):
        sale = self.posted_sale('evidence-sale')
        detail = self.add_payments(sale, [{
            'tender': 'e_transfer', 'amount_cents': 4520,
        }], 'evidence-payment').get_json()
        return detail, detail['payments'][0]['id']

    @staticmethod
    def image_bytes(fmt='PNG'):
        output = io.BytesIO()
        Image.new('RGB', (20, 12), '#16a34a').save(output, format=fmt, exif=b'test-metadata')
        return output.getvalue()

    def upload(self, payment_id, key='evidence-upload', content=None, role='staff'):
        return self.client.post(
            f'/api/payments/{payment_id}/evidence',
            headers={**self.headers(role), 'Idempotency-Key': key},
            data={'image': (io.BytesIO(content or self.image_bytes()), 'receipt.png')},
            content_type='multipart/form-data',
        )

    def test_upload_is_normalized_private_and_replayed(self):
        _, payment_id = self.payment()
        first = self.upload(payment_id)
        replay = self.upload(payment_id)
        self.assertEqual(first.status_code, 201, first.get_json())
        self.assertEqual(replay.get_json(), first.get_json())
        evidence_id = first.get_json()['evidence_id']
        content = self.client.get(
            f'/api/payment-evidence/{evidence_id}/content', headers=self.headers()
        )
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(content.headers['X-Content-Type-Options'], 'nosniff')
        self.assertNotIn(b'test-metadata', content.data)
        content.close()
        direct = self.client.get('/uploads/payment_evidence/guessed.png')
        self.assertEqual(direct.status_code, 404)
        with closing(self.connect()) as con:
            self.assertEqual(con.execute(
                'SELECT COUNT(*) FROM payment_evidence'
            ).fetchone()[0], 1)

    def test_invalid_animated_and_non_image_files_are_rejected(self):
        _, payment_id = self.payment()
        invalid = self.upload(payment_id, 'invalid-upload', b'<svg>bad</svg>')
        self.assertEqual(invalid.status_code, 400, invalid.get_json())
        animation = io.BytesIO()
        frames = [Image.new('RGB', (5, 5), color) for color in ('red', 'blue')]
        frames[0].save(animation, format='WEBP', save_all=True,
                       append_images=frames[1:], duration=100, loop=0)
        animated = self.upload(payment_id, 'animated-upload', animation.getvalue())
        self.assertEqual(animated.status_code, 400, animated.get_json())

    def test_only_uploader_or_scoped_manager_can_read_bytes(self):
        _, payment_id = self.payment()
        evidence = self.upload(payment_id).get_json()
        evidence_id = evidence['evidence_id']
        other_staff = self.client.get(
            f'/api/payment-evidence/{evidence_id}/content',
            headers=self.headers('staff:other'),
        )
        self.assertEqual(other_staff.status_code, 403)
        manager = self.client.get(
            f'/api/payment-evidence/{evidence_id}/content',
            headers=self.headers('manager'),
        )
        self.assertEqual(manager.status_code, 200)
        manager.close()
        viewer = self.client.get(
            f'/api/payment-evidence/{evidence_id}/content', headers=self.headers('viewer')
        )
        self.assertEqual(viewer.status_code, 403)
        anonymous = self.client.get(f'/api/payment-evidence/{evidence_id}/content')
        self.assertEqual(anonymous.status_code, 401)

    def test_other_store_manager_is_denied_and_review_does_not_verify_payment(self):
        detail, payment_id = self.payment()
        evidence = self.upload(payment_id).get_json()
        evidence_id = evidence['evidence_id']
        with closing(self.connect()) as con:
            mk_id = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|other', ?)",
                (mk_id,),
            )
            con.commit()
        denied = self.client.get(
            f'/api/payment-evidence/{evidence_id}/content',
            headers=self.headers('manager:other'),
        )
        self.assertEqual(denied.status_code, 403)
        reviewed = self.client.post(
            f'/api/payment-evidence/{evidence_id}/review',
            headers={**self.headers('manager'), 'Idempotency-Key': 'evidence-review'},
            json={'decision': 'accepted', 'reason': 'Matches transfer record'},
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.get_json())
        sale_detail = self.client.get(
            f"/api/sale-documents/{detail['sale_id']}", headers=self.headers('manager')
        ).get_json()
        self.assertEqual(sale_detail['payments'][0]['evidence'][0]['status'], 'accepted')
        self.assertNotIn('object_id', sale_detail['payments'][0]['evidence'][0])
        with closing(self.connect()) as con:
            state = con.execute(
                'SELECT state FROM sale_payments WHERE id=?', (payment_id,)
            ).fetchone()[0]
        self.assertEqual(state, 'recorded')

    def test_rejected_evidence_is_retained_when_replaced(self):
        _, payment_id = self.payment()
        first = self.upload(payment_id, 'first-evidence').get_json()
        self.client.post(
            f"/api/payment-evidence/{first['evidence_id']}/review",
            headers={**self.headers('manager'), 'Idempotency-Key': 'reject-evidence'},
            json={'decision': 'rejected', 'reason': 'Unreadable amount'},
        )
        second = self.upload(payment_id, 'replacement-evidence', self.image_bytes('JPEG'))
        self.assertEqual(second.status_code, 201, second.get_json())
        with closing(self.connect()) as con:
            rows = con.execute(
                'SELECT status FROM payment_evidence WHERE payment_id=? ORDER BY id',
                (payment_id,),
            ).fetchall()
        self.assertEqual([row['status'] for row in rows], ['rejected', 'pending'])

    def test_database_then_private_files_restore_matches_manifest(self):
        _, payment_id = self.payment()
        uploaded = self.upload(payment_id, 'backup-evidence').get_json()
        backup_db = self.upload_dir / 'restore.db'
        restored_files = self.upload_dir / 'restored-evidence'
        with closing(self.connect()) as source, closing(sqlite3.connect(backup_db)) as target:
            source.backup(target)
        shutil.copytree(self.upload_dir / 'payment_evidence', restored_files)
        with closing(sqlite3.connect(backup_db)) as restored:
            manifest = restored.execute(
                'SELECT object_id, byte_size FROM payment_evidence ORDER BY id'
            ).fetchall()
        self.assertEqual(len(manifest), 1)
        object_id, byte_size = manifest[0]
        self.assertEqual(uploaded['byte_size'], byte_size)
        self.assertEqual((restored_files / object_id).stat().st_size, byte_size)
