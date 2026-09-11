from contextlib import closing
from support import IsolatedApiCase


class TodayShiftTests(IsolatedApiCase):
    def test_personal_shifts_remain_available_without_inventory_access(self):
        with closing(self.connect()) as con:
            con.execute("DELETE FROM inventory_access WHERE auth0_sub='auth0|staff'")
            employee_id = con.execute("INSERT INTO employees(auth0_id,name) VALUES ('auth0|staff','Staff')").lastrowid
            con.execute("INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,store_id,position) VALUES (?,'2026-11-20','09:00','17:00','manager',?,'Floor')", (employee_id,self.store_id))
            con.commit()
        response=self.client.get('/api/schedule/shifts/me?start=2026-09-08&store_code=ALL',headers=self.headers('staff'))
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual(response.get_json()[0]['date'],'2026-11-20')
        self.assertEqual(response.get_json()[0]['store_code'],'DT')
