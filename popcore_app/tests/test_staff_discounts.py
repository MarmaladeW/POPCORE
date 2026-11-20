"""员工折扣 Staff discounts (owner, 2026-11-19): "each 正式员工 and above have 4 discount per month, we wanna keep track
of that". 日报 员工折扣 lines become use records (one use is one product: "purchasing ten smiski hipper is still one
employee discount"), the 4 uses come back each calendar month, names link to people or wait for a manager, whose pick
is remembered, and a re-paste never undoes a manager's fix. Staff see the page; managers change it."""
import json
from contextlib import closing

from support import IsolatedApiCase

from blueprints import staff_discounts as staff_discounts_bp
from staff_discounts import month_bounds, parse_line

CELIA_LINE = 'Celia使用员工折扣购入Angry Molly 迪士尼公主毛绒*2,emt 54,本月剩余0次'  # DT, 2026-11-13
ZM_LINE = 'zm使用员工折扣购入SA Animal ver2*1,卡机18.07,本月剩余3次'                  # DT, 2026-11-05
MANAGER = {'Authorization': 'Bearer manager'}
STAFF = {'Authorization': 'Bearer staff'}
VIEWER = {'Authorization': 'Bearer viewer'}


class StaffDiscountCase(IsolatedApiCase):
    """Eight people across the ranks (one left, one a look-only account) and DT's two real report lines."""

    def setUp(self):
        super().setUp()
        self.app.register_blueprint(staff_discounts_bp.bp)
        with closing(self.connect()) as con:
            self.people = {}
            for sub, name, rank, active in (
                    ('auth0|manager', 'Mia', 'store_manager', 1), ('auth0|ben', 'Ben', 'assistant_manager', 1),
                    ('auth0|kai', 'Kai', 'senior', 1), ('auth0|zimo', 'Zimo', 'regular', 1),
                    ('auth0|celia', 'Celia', 'regular', 1), ('auth0|staff', 'Amy', 'regular', 1),
                    ('auth0|tina', 'Tina', 'trainee', 1), ('auth0|counter', 'Counter', None, 1),
                    ('auth0|old', 'Olive', 'regular', 0)):
                self.people[name] = con.execute(
                    'INSERT INTO employees(auth0_id, name, rank, is_active) VALUES (?, ?, ?, ?)',
                    (sub, name, rank, active)).lastrowid
            con.commit()
        self.report('2026-11-13', [CELIA_LINE])
        self.report('2026-11-05', [ZM_LINE])

    def report(self, day, lines, store='DT'):
        """Save a day's 日报 员工折扣 lines as the report paste does (a re-paste replaces the day)."""
        with closing(self.connect()) as con:
            con.execute('DELETE FROM daily_report_metadata WHERE date=? AND store=?', (day, store))
            con.execute('INSERT INTO daily_report_metadata(date, store, employee_discounts) VALUES (?, ?, ?)',
                        (day, store, json.dumps(lines, ensure_ascii=False)))
            con.commit()

    def page(self, month='2026-11', headers=MANAGER):
        response = self.client.get('/api/staff-discounts' + (f'?month={month}' if month else ''), headers=headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def person(self, page, name):
        return next(person for person in page['people'] if person['name'] == name)

    def entry(self, page, text):
        return next(entry for entry in page['uses'] + page['waiting'] + page['ignored'] if entry.get('raw_text') == text)

    def post(self, path, body=None, headers=MANAGER, key=None):
        return self.client.post('/api/staff-discounts' + path, json=body or {},
                                headers={**headers, **({'Idempotency-Key': key} if key else {})})

    def add(self, name, day, items, amount=None, store='DT', key=None):
        response = self.post('', {'employee_id': self.people[name], 'date': day, 'store': store, 'items': items,
                                  'amount': amount, 'payment': 'cash'}, key=key)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['use']


class StaffDiscountTests(StaffDiscountCase):
    def test_the_two_real_lines_read_as_one_use_each(self):
        self.assertEqual(parse_line(CELIA_LINE), {
            'name': 'Celia', 'items': [{'name': 'Angry Molly 迪士尼公主毛绒', 'qty': 2}], 'uses': 1,
            'amount_cents': 5400, 'payment': 'emt', 'readable': True})
        self.assertEqual(parse_line(ZM_LINE), {
            'name': 'zm', 'items': [{'name': 'SA Animal ver2', 'qty': 1}], 'uses': 1,
            'amount_cents': 1807, 'payment': 'card', 'readable': True})

    def test_harder_lines(self):
        two = parse_line('Amy使用员工折扣购入Labubu*1，Smiski Hipper×2，现金30，本月剩余2次')  # full-width commas
        self.assertEqual((two['name'], two['items'], two['uses'], two['amount_cents'], two['payment']),
                         ('Amy', [{'name': 'Labubu', 'qty': 1}, {'name': 'Smiski Hipper', 'qty': 2}], 2, 3000, 'cash'))
        joined = parse_line('Amy使用员工折扣购买Crybaby x 2+Twinkle X1、Hacipupu*3 卡机18.07今月剩余1次')
        self.assertEqual([(item['name'], item['qty']) for item in joined['items']],
                         [('Crybaby', 2), ('Twinkle', 1), ('Hacipupu', 3)])
        self.assertEqual((joined['uses'], joined['amount_cents'], joined['payment']), (3, 1807, 'card'))
        no_amount = parse_line('Kai使用员工折扣购入Labubu*1')
        self.assertEqual((no_amount['uses'], no_amount['amount_cents'], no_amount['payment'], no_amount['readable']),
                         (1, None, None, True))
        # The parser's own joins and dates: "X*1, EMT24, 本月剩余3次" and a day written first.
        dated = parse_line('2026.9.9 staff使用员工折扣购入Test Product*1, EMT24, 本月剩余3次')
        self.assertEqual((dated['name'], dated['uses'], dated['amount_cents']), ('staff', 1, 2400))
        self.assertEqual(parse_line('Celia 使用员工折扣，购入Labubu*1')['name'], 'Celia')
        for unreadable in ('Celia员工折扣两个', 'Celia使用员工折扣购入Labubu, emt 20', 'Celia使用员工折扣购入'):
            with self.subTest(unreadable=unreadable):
                self.assertFalse(parse_line(unreadable)['readable'])

    def test_ten_pieces_of_one_product_are_one_use(self):
        read = parse_line('Celia使用员工折扣购入Smiski Hipper*10, card 40')
        self.assertEqual((read['uses'], read['items']), (1, [{'name': 'Smiski Hipper', 'qty': 10}]))
        # The same product written twice in one purchase is still one product.
        self.assertEqual(parse_line('Celia使用员工折扣购入Smiski Hipper*4, smiski hipper*6, card 40')['uses'], 1)
        use = self.add('Kai', '2026-11-10', [{'name': 'Smiski Hipper', 'qty': 10}], '40')
        self.assertEqual(use['uses'], 1)

    def test_the_real_lines_show_up_and_zm_waits_for_a_manager(self):
        page = self.page()
        self.assertEqual(self.person(page, 'Celia')['used'], 1)
        self.assertEqual(self.person(page, 'Celia')['left'], 3)
        [celia] = page['uses']
        self.assertEqual((celia['date'], celia['store'], celia['source'], celia['amount_cents'], celia['payment']),
                         ('2026-11-13', 'DT', 'report', 5400, 'emt'))
        [zm] = page['waiting']
        self.assertEqual((zm['written_name'], zm['waiting_for'], zm['raw_text']), ('zm', 'person', ZM_LINE))
        # The month's money: both lines (who it was does not change what was paid).
        self.assertEqual(page['totals']['amount_cents'], 7207)
        self.assertEqual([(total['store'], total['amount_cents']) for total in page['totals']['stores']],
                         [('DT', 7207)])
        # Reading again adds nothing.
        self.page()
        with closing(self.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM staff_discount_uses').fetchone()[0], 2)

    def test_an_unreadable_line_waits_with_its_text(self):
        self.report('2026-11-14', ['Celia员工折扣两个毛绒'])
        waiting = self.entry(self.page(), 'Celia员工折扣两个毛绒')
        self.assertEqual((waiting['state'], waiting['uses']), ('waiting', 0))
        response = self.post(f"/{waiting['id']}/fix", {'employee_id': self.people['Celia'], 'uses': 2, 'amount': '30'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.person(self.page(), 'Celia')['used'], 3)

    def test_the_four_uses_come_back_every_calendar_month(self):
        for day in ('2026-10-01', '2026-10-09', '2026-10-20', '2026-10-31'):
            self.add('Celia', day, [{'name': 'Labubu', 'qty': 1}])
        october, november = self.page('2026-10'), self.page('2026-11')
        self.assertEqual((self.person(october, 'Celia')['used'], self.person(october, 'Celia')['left']), (4, 0))
        self.assertEqual((self.person(november, 'Celia')['used'], self.person(november, 'Celia')['left']), (1, 3))
        self.assertEqual(month_bounds('2026-12'), ('2026-12-01', '2027-01-01'))
        self.assertEqual(month_bounds('2026-11'), ('2026-11-01', '2026-12-01'))
        self.assertEqual(self.client.get('/api/staff-discounts?month=2026-13', headers=MANAGER).status_code, 400)
        # No month: this month, in Toronto.
        page = self.page(None)
        self.assertEqual(page['month'], page['this_month'])

    def test_over_four_stands_out(self):
        for index in range(4):
            self.add('Celia', f'2026-11-0{index + 1}', [{'name': f'Product {index}', 'qty': 1}])
        celia = self.person(self.page(), 'Celia')
        self.assertEqual((celia['used'], celia['left'], celia['over']), (5, 0, 1))

    def test_eligibility_follows_rank(self):
        self.add('Tina', '2026-11-12', [{'name': 'Labubu', 'qty': 1}])
        page = self.page()
        names = [person['name'] for person in page['people']]
        # 正式员工 and above, active; 培训员工 only with a use, marked not eligible; no rank, no use: not listed.
        self.assertEqual(set(names), {'Mia', 'Ben', 'Kai', 'Amy', 'Celia', 'Zimo', 'Tina'})
        tina = self.person(page, 'Tina')
        self.assertEqual((tina['eligible'], tina['used'], tina['left'], tina['rank_label']), (False, 1, None, '培训员工'))
        self.assertTrue(self.person(page, 'Kai')['eligible'])
        self.assertEqual(self.person(page, 'Kai')['left'], 4)

    def test_people_are_listed_in_rank_order(self):
        names = [person['name'] for person in self.page()['people']]
        self.assertEqual(names, ['Mia', 'Ben', 'Kai', 'Amy', 'Celia', 'Zimo'])
        self.assertEqual([person['name'] for person in self.page()['employees']],
                         ['Mia', 'Ben', 'Kai', 'Amy', 'Celia', 'Zimo', 'Tina', 'Counter'])

    def test_a_picked_name_is_remembered(self):
        self.report('2026-11-07', ['zm使用员工折扣购入Labubu*1, emt 20'])
        page = self.page()
        self.assertEqual(len(page['waiting']), 2)
        zm = self.entry(page, ZM_LINE)
        response = self.post(f"/{zm['id']}/fix", {'employee_id': self.people['Zimo']})
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(self.connect()) as con:
            alias = con.execute('SELECT alias, employee_id FROM staff_discount_aliases').fetchall()
        self.assertEqual([tuple(row) for row in alias], [('zm', self.people['Zimo'])])
        # The other line waiting on "zm" follows, and so does a later report writing "ZM".
        self.report('2026-11-18', ['ZM使用员工折扣购入Crybaby*1, 卡机25'])
        page = self.page()
        self.assertFalse(page['waiting'])
        self.assertEqual(self.person(page, 'Zimo')['used'], 3)
        # An exact name needs no pick, in any case.
        self.report('2026-11-19', ['celia使用员工折扣购入Twinkle*1'])
        self.assertEqual(self.person(self.page(), 'Celia')['used'], 2)

    def test_a_re_paste_keeps_a_managers_fix_and_counts_once(self):
        celia = self.entry(self.page(), CELIA_LINE)
        response = self.post(f"/{celia['id']}/fix", {'uses': 2, 'amount': '50.5'})
        self.assertEqual(response.status_code, 200, response.get_json())
        # The report is pasted again, with one more line.
        extra = 'Kai使用员工折扣购入Labubu*1, cash 20'
        self.report('2026-11-13', [CELIA_LINE, extra])
        page = self.page()
        fixed = self.entry(page, CELIA_LINE)
        self.assertEqual((fixed['uses'], fixed['amount_cents'], fixed['edited']), (2, 5050, True))
        self.assertEqual(self.person(page, 'Celia')['used'], 2)
        self.assertEqual(self.person(page, 'Kai')['used'], 1)
        # Pasted once more without both lines: the line nobody fixed goes, the fixed one stays.
        self.report('2026-11-13', [])
        page = self.page()
        self.assertEqual(self.person(page, 'Kai')['used'], 0)
        self.assertEqual(self.person(page, 'Celia')['used'], 2)
        # The same line written twice on one day is two purchases.
        twice = 'Kai使用员工折扣购入Crybaby*1, cash 20'
        self.report('2026-11-16', [twice, twice])
        self.page()
        self.assertEqual(self.person(self.page(), 'Kai')['used'], 2)

    def test_the_report_paste_itself_saves_lines_the_page_reads(self):
        with closing(self.connect()) as con:  # report writes need access to the report's store
            con.execute("INSERT INTO inventory_access(auth0_sub, store_id) VALUES ('auth0|manager', ?)", (self.store_id,))
            con.commit()
        response = self.client.post('/api/sales/submit_daily_report', headers=MANAGER, json={
            'date': '2026-11-17', 'store_code': 'DT', 'items': [],
            'report_metadata': {'employee_discounts': ['Ben使用员工折扣购入Labubu*1，EMT 22，本月剩余3次']}})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.person(self.page(), 'Ben')['used'], 1)

    def test_added_by_hand_ignored_and_counted_again(self):
        use = self.add('Kai', '2026-11-11', [{'name': 'Labubu', 'qty': 1}, {'name': 'Crybaby', 'qty': 2}], '42.10',
                       store='MK', key='add-1')
        self.assertEqual((use['source'], use['uses'], use['amount_cents'], use['created_by_name']),
                         ('manual', 2, 4210, 'Mia'))
        # A retried add is the same record.
        self.assertEqual(self.add('Kai', '2026-11-11', [{'name': 'Labubu', 'qty': 1}], key='add-1')['id'], use['id'])
        page = self.page()
        self.assertEqual(self.person(page, 'Kai')['used'], 2)
        self.assertEqual([(total['store'], total['amount_cents']) for total in page['totals']['stores']],
                         [('DT', 7207), ('MK', 4210)])
        self.assertEqual(self.post(f"/{use['id']}/ignore", {'reason': ' '}).status_code, 400)
        response = self.post(f"/{use['id']}/ignore", {'reason': '买错了 Wrong purchase'})
        self.assertEqual(response.status_code, 200, response.get_json())
        page = self.page()
        self.assertEqual(self.person(page, 'Kai')['used'], 0)
        self.assertEqual([entry['ignored_reason'] for entry in page['ignored']], ['买错了 Wrong purchase'])
        self.assertEqual(page['totals']['amount_cents'], 7207)
        self.assertEqual(self.post(f"/{use['id']}/restore").status_code, 200)
        self.assertEqual(self.person(self.page(), 'Kai')['used'], 2)
        for body in ({'employee_id': self.people['Olive'], 'date': '2026-11-11', 'store': 'DT', 'items': [{'name': 'X'}]},
                     {'employee_id': self.people['Kai'], 'date': '2026-11-11', 'store': 'DT', 'items': []},
                     {'employee_id': self.people['Kai'], 'date': '2026-11-11', 'store': 'ZZ', 'items': [{'name': 'X'}]},
                     {'employee_id': self.people['Kai'], 'date': '2026-11-11', 'store': 'DT', 'items': [{'name': 'X'}],
                      'amount': '1.234'}):
            with self.subTest(body=body):
                self.assertEqual(self.post('', body).status_code, 400)
        self.assertEqual(self.post('/99999/fix', {'uses': 1}).status_code, 404)

    def test_staff_see_the_page_and_change_nothing_and_viewers_cannot_open_it(self):
        page = self.page(headers=STAFF)
        self.assertFalse(page['can_manage'])
        self.assertEqual(self.person(page, 'Celia')['used'], 1)
        self.assertNotIn('totals', page)
        self.assertNotIn('employees', page)
        for entry in page['uses'] + page['waiting']:
            self.assertNotIn('amount_cents', entry)
            self.assertNotIn('raw_text', entry)
        celia = self.entry(self.page(), CELIA_LINE)
        before = self.snapshot(['staff_discount_uses', 'staff_discount_aliases'])
        for path, body in (('', {'employee_id': self.people['Amy'], 'date': '2026-11-11', 'store': 'DT',
                                 'items': [{'name': 'Labubu', 'qty': 1}]}),
                           (f"/{celia['id']}/fix", {'uses': 3}),
                           (f"/{celia['id']}/ignore", {'reason': 'no'}),
                           (f"/{celia['id']}/restore", {})):
            for headers in (STAFF, VIEWER):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.post(path, body, headers=headers).status_code, 403)
        self.assertEqual(self.snapshot(['staff_discount_uses', 'staff_discount_aliases']), before)
        self.assertEqual(self.client.get('/api/staff-discounts', headers=VIEWER).status_code, 403)
