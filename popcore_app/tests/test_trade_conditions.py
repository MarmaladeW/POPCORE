import io
from contextlib import closing

from PIL import Image

from test_trades import TradeTests


class TradeConditionTests(TradeTests):
    @staticmethod
    def image_bytes():
        output = io.BytesIO()
        Image.new('RGB', (20, 12), '#ef4444').save(
            output, format='PNG', exif=b'private-metadata'
        )
        return output.getvalue()

    def create_case(self):
        opened = self.open_slot(self.create_slot(), 'condition-open')
        response = self.client.post(
            '/api/condition-cases',
            headers={**self.headers('staff'), 'Idempotency-Key': 'condition-create'},
            json={
                'unit_id': opened['unit_id'],
                'observed_condition': 'Paint mark on face',
                'disclosed_condition': 'Complete, light shelf wear',
                'reason': 'Customer reported condition after purchase review',
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def test_private_condition_evidence_is_normalized_and_scoped(self):
        case = self.create_case()
        uploaded = self.client.post(
            f"/api/condition-cases/{case['case_id']}/evidence",
            headers={**self.headers('staff'), 'Idempotency-Key': 'condition-image'},
            data={'image': (io.BytesIO(self.image_bytes()), 'condition.png')},
            content_type='multipart/form-data',
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.get_json())
        evidence_id = uploaded.get_json()['evidence_id']
        own = self.client.get(
            f'/api/condition-evidence/{evidence_id}/content',
            headers=self.headers('staff'),
        )
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.headers['Cache-Control'], 'private, no-store')
        self.assertNotIn(b'private-metadata', own.data)
        own.close()

        with closing(self.connect()) as con:
            con.execute(
                "INSERT INTO inventory_access(auth0_sub, store_id) VALUES (?, ?)",
                ('auth0|other', self.store_id),
            )
            con.commit()
        other_staff = self.client.get(
            f'/api/condition-evidence/{evidence_id}/content',
            headers=self.headers('staff:other'),
        )
        manager = self.client.get(
            f'/api/condition-evidence/{evidence_id}/content',
            headers=self.headers('manager'),
        )
        self.assertEqual(other_staff.status_code, 403)
        self.assertEqual(manager.status_code, 200)
        manager.close()

    def test_manager_decision_is_append_only_and_does_not_change_stock(self):
        case = self.create_case()
        before = self.snapshot(['inventory_balances', 'sale_payments'])
        decided = self.client.post(
            f"/api/condition-cases/{case['case_id']}/decision",
            headers={**self.headers('manager'), 'Idempotency-Key': 'condition-decision'},
            json={
                'expected_version': case['version'],
                'disposition': 'referred_for_separate_review',
                'reason': 'Needs a separately authorized refund or return decision',
            },
        )
        replay = self.client.post(
            f"/api/condition-cases/{case['case_id']}/decision",
            headers={**self.headers('manager'), 'Idempotency-Key': 'condition-decision'},
            json={
                'expected_version': case['version'],
                'disposition': 'referred_for_separate_review',
                'reason': 'Needs a separately authorized refund or return decision',
            },
        )
        self.assertEqual(decided.status_code, 200, decided.get_json())
        self.assertEqual(replay.get_json(), decided.get_json())
        self.assertEqual(self.snapshot(['inventory_balances', 'sale_payments']), before)
        with closing(self.connect()) as con:
            self.assertEqual(
                con.execute('SELECT COUNT(*) FROM condition_events WHERE case_id=?',
                            (case['case_id'],)).fetchone()[0], 2,
            )

    def test_condition_case_rejects_mixed_unit_and_sale_sources(self):
        opened = self.open_slot(self.create_slot(), 'mixed-source-open')
        response = self.client.post(
            '/api/condition-cases',
            headers={**self.headers('staff'), 'Idempotency-Key': 'mixed-source'},
            json={'unit_id': opened['unit_id'], 'sale_id': 999, 'sale_line_no': 1,
                  'observed_condition': 'mark', 'reason': 'review'},
        )
        self.assertEqual(response.status_code, 400, response.get_json())
