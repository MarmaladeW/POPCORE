from contextlib import closing

from test_receiving import ReceivingFixture


class DesignInventoryFlowTests(ReceivingFixture):
    def test_receive_identify_and_sell_one_design_preserves_other_stock_and_replays(self):
        requests = []

        def post(path, body, key, status=200, role='staff'):
            response = self.client.post(path, json=body, headers={
                **self.headers(role), 'Idempotency-Key': key,
            })
            self.assertEqual(response.status_code, status, response.get_json())
            result = response.get_json()
            requests.append((path, body, key, role, status, result))
            return result

        roster = post('/api/product-series/setup', {
            'name': 'Garden', 'design_names': ['Red', 'Blue', 'Green', 'Gold', 'Silver', 'Black'],
        }, 'flow-roster', 201, 'manager')
        red, blue = roster['product_ids'][:2]
        with closing(self.connect()) as con:
            # The source is an explicitly reviewed random-box SKU, never inferred from the roster.
            con.execute("UPDATE products SET series_id=?,stock_form='random_box',stock_unit='box' WHERE id=?",
                        (roster['id'], self.product_id))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM inventory_documents').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM inventory_balances').fetchone()[0], 0)
            con.commit()

        receipts = []
        for key, lines in (
            ('initial', [
                {'product_id': self.product_id, 'unit': 'box', 'saleable_quantity': 5},
                {'product_id': blue, 'unit': 'piece', 'saleable_quantity': 7},
            ]),
            ('red', [{'product_id': red, 'unit': 'piece', 'saleable_quantity': 3, 'damaged_quantity': 1}]),
        ):
            receipt = post('/api/goods/receipts', {
                'store_id': self.store_id, 'destination_location_id': self.floor,
                'business_date': '2026-09-30', 'shipment_reference': 'FLOW-' + key,
                'lines': lines,
            }, 'flow-receipt-create-' + key, 201)
            receipts.append(post(f"/api/goods/receipts/{receipt['id']}/post", {
                'expected_version': receipt['version'],
            }, 'flow-receipt-post-' + key))

        def read_balances():
            response = self.client.get('/api/inventory/series', headers=self.headers(),
                                       query_string={'store_code': 'DT', 'series_id': roster['id']})
            self.assertEqual(response.status_code, 200, response.get_json())
            return {(product['id'], balance['disposition']): balance
                    for product in response.get_json()['series'][0]['products']
                    for balance in product['balances'] if balance['location_id'] == self.floor}

        before = read_balances()
        self.assertEqual(before[red, 'saleable']['quantity'], 3)
        self.assertEqual(before[red, 'damaged']['quantity'], 1)
        for product_id in roster['product_ids'][2:]:
            self.assertEqual(before[product_id, 'saleable']['quantity'], 0)
            self.assertEqual(before[product_id, 'saleable']['version'], 0)
        identified = post('/api/goods/identify', {
            'source_product_id': self.product_id, 'design_product_id': red,
            'location_id': self.floor, 'quantity': 2,
            'source_version': before[self.product_id, 'saleable']['version'],
            'target_version': before[red, 'saleable']['version'],
            'business_date': '2026-09-30', 'reason': 'Opened and visually confirmed Red',
        }, 'flow-identify')
        after_identify = read_balances()
        self.assertEqual(after_identify[red, 'saleable']['quantity'], 5)
        self.assertEqual(after_identify[self.product_id, 'saleable']['quantity'], 3)
        self.assertEqual(after_identify[blue, 'saleable'], before[blue, 'saleable'])

        sale = post('/api/sale-documents', {
            'store_id': self.store_id, 'business_date': '2026-09-30', 'entry_mode': 'planned_entry',
            'source': {'system': 'manual', 'account': 'DT', 'reference': 'FLOW-RED-SALE'},
            'subtotal_cents': 8000, 'source_tax_cents': 1040, 'gross_cents': 9040,
            'reduction_cents': 0, 'rounding_cents': 0, 'collected_cents': 9040,
            'lines': [{'product_id': red, 'unit': 'piece', 'quantity': 4,
                       'unit_price_cents': 2000, 'source_tax_cents': 1040}],
        }, 'flow-sale-create', 201)
        sold = post(f"/api/sale-documents/{sale['sale_id']}/post", {
            'expected_version': sale['version'],
        }, 'flow-sale-post')
        self.assertEqual(sold['allocation_status'], 'allocated')
        final = read_balances()
        self.assertEqual(final[red, 'saleable']['quantity'], 1)
        self.assertEqual(final[red, 'damaged'], before[red, 'damaged'])
        self.assertEqual(final[blue, 'saleable'], before[blue, 'saleable'])
        self.assertEqual(final[self.product_id, 'saleable'], after_identify[self.product_id, 'saleable'])
        self.assertTrue(all(balance['quantity'] == 0 for (product_id, disposition), balance in final.items()
                            if disposition == 'trade'))

        history = self.client.get(f"/api/inventory/series/{roster['id']}/history?store_code=DT",
                                  headers=self.headers())
        self.assertEqual(history.status_code, 200, history.get_json())
        movements = history.get_json()['items']
        self.assertEqual(len(movements), 7)
        self.assertEqual({row['document_id'] for row in movements}, {
            *(receipt['inventory_document_id'] for receipt in receipts),
            identified['consume_document_id'], identified['receipt_document_id'],
            sold['inventory_document_id'],
        })
        self.assertEqual([(row['product_id'], row['quantity']) for row in movements
                          if row['source_type'] == 'sale_document'], [(red, -4)])
        self.assertTrue(all(row['product_name'] == 'Garden · Red' for row in movements if row['product_id'] == red))
        self.assertEqual(sum(row['quantity'] for row in movements), 12)
        for product_id in (self.product_id, *roster['product_ids']):
            self.assertEqual(sum(row['quantity'] for row in movements if row['product_id'] == product_id),
                             sum(balance['quantity'] for (pid, disposition), balance in final.items() if pid == product_id))
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT product_name_snapshot FROM sale_lines WHERE sale_id=?',
                                         (sale['sale_id'],)).fetchone()[0], 'Garden · Red')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM inventory_documents').fetchone()[0], 5)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM trade_units').fetchone()[0], 0)

        tables = ('products', 'product_series', 'inventory_documents', 'inventory_document_lines',
                  'inventory_movements', 'inventory_balances', 'operation_requests',
                  'goods_receipts', 'goods_receipt_lines', 'sale_documents', 'sale_lines',
                  'stock', 'stock_transactions', 'stock_movements', 'trade_units')
        snapshot = self.snapshot(tables)
        for path, body, key, role, status, result in requests:
            with self.subTest(replay=key):
                replay = self.client.post(path, json=body, headers={
                    **self.headers(role), 'Idempotency-Key': key,
                })
                self.assertEqual((replay.status_code, replay.get_json()), (status, result))
        self.assertEqual(self.snapshot(tables), snapshot)
