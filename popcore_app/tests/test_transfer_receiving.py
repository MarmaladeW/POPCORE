from contextlib import closing

from support import IsolatedApiCase


class TransferReceivingTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        with closing(self.connect()) as con:
            self.other_store = con.execute("SELECT id FROM stores WHERE code='MK'").fetchone()[0]
            self.source = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='warehouse'", (self.other_store,)).fetchone()[0]
            self.destination = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'", (self.store_id,)).fetchone()[0]
            con.execute("INSERT INTO inventory_access(auth0_sub,store_id) VALUES ('auth0|staff',?)", (self.store_id,))
            con.commit()

    def delivery(self, *, status='active', date='2026-09-01', reverse=False, kind='transfer'):
        with closing(self.connect()) as con:
            source, destination = (self.destination, self.source) if reverse else (self.source, self.destination)
            delivery_id = con.execute('''INSERT INTO inventory_deliveries
                (kind,source_location_id,destination_location_id,business_date,status,created_by)
                VALUES (?,?,?,?,?,'auth0|manager')''', (kind,source,destination,date,status)).lastrowid
            dispatched, received = (0,0) if status == 'planned' else (5,2)
            con.execute('''INSERT INTO inventory_delivery_lines
                (delivery_id,line_no,product_id,native_unit,requested_quantity,dispatched_quantity,received_quantity)
                VALUES (?,1,?,'piece',5,?,?)''', (delivery_id,self.product_id,dispatched,received))
            con.commit()
            return delivery_id

    def test_lists_unfinished_incoming_across_dates_with_readable_quantities(self):
        older = self.delivery()
        planned = self.delivery(status='planned', date='2026-09-29')
        self.delivery(status='completed')
        self.delivery(status='cancelled')
        self.delivery(reverse=True)
        self.delivery(kind='restock')
        before = self.snapshot(['inventory_deliveries','inventory_delivery_lines','inventory_balances','inventory_documents'])
        response = self.client.get(f'/api/goods/transfers?store_id={self.store_id}', headers=self.headers())
        self.assertEqual(response.status_code, 200, response.get_json())
        rows = response.get_json()
        self.assertEqual([row['id'] for row in rows], [older,planned])
        self.assertEqual(rows[0]['business_date'], '2026-09-01')
        self.assertEqual(rows[0]['source_store_name'], 'Markham')
        self.assertTrue(rows[0]['source_location_name'])
        self.assertTrue(rows[0]['destination_location_name'])
        self.assertEqual(rows[0]['lines'][0], {'line_no':1,'product_id':self.product_id,'product_name':'Test Product','sku':'TEST-1','native_unit':'piece','outstanding_transit':3,'awaiting_dispatch':0})
        self.assertEqual(rows[1]['lines'][0]['awaiting_dispatch'], 5)
        self.assertEqual(before, self.snapshot(before.keys()))
        detail = self.client.get(f'/api/goods/transfers/{older}', headers=self.headers())
        self.assertEqual(detail.status_code, 200)  # Destination-only access can resume.

    def test_requires_explicit_inventory_access_to_selected_destination(self):
        self.delivery()
        for role, store in [('staff',self.other_store),('manager',self.store_id),('admin',self.store_id),('viewer',self.store_id)]:
            with self.subTest(role=role, store=store):
                response = self.client.get(f'/api/goods/transfers?store_id={store}', headers=self.headers(role))
                self.assertEqual(response.status_code, 403)
        with closing(self.connect()) as con:
            con.execute('DELETE FROM inventory_access')
            con.commit()
        self.assertEqual(self.client.get(f'/api/goods/transfers?store_id={self.store_id}', headers=self.headers()).status_code, 403)

    def test_empty_and_invalid_store_scope(self):
        response = self.client.get(f'/api/goods/transfers?store_id={self.store_id}', headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [])
        for query in ('','?store_id=ALL','?store_id=0','?store_id=-1'):
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/api/goods/transfers'+query, headers=self.headers()).status_code, 400)
