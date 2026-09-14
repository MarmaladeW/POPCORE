from support import IsolatedApiCase


class ScheduleReminderTests(IsolatedApiCase):
    def test_shared_reminder_permissions_and_persistence(self):
        path = '/api/schedule/reminder'
        self.assertEqual(self.client.get('/api/schedule/config', headers=self.headers()).json['schedule_reminder'], '提前十分钟到！')
        for role in ('staff', 'viewer'):
            self.assertEqual(self.client.put(path, json={'content': 'Changed'}, headers=self.headers(role)).status_code, 403)
        for role in ('manager', 'admin'):
            content = f'提前十分钟到！\n{role} reminder'
            result = self.client.put(path, json={'content': content}, headers=self.headers(role))
            self.assertEqual(result.status_code, 200, result.json)
            self.assertEqual(self.client.get('/api/schedule/config', headers=self.headers('staff:other')).json['schedule_reminder'], content)

    def test_invalid_reminders_do_not_overwrite_saved_text(self):
        path = '/api/schedule/reminder'
        self.assertEqual(self.client.put(path, json={'content': 'Saved'}, headers=self.headers('manager')).status_code, 200)
        for data in ([], {}, {'content': None}, {'content': 42}, {'content': ' '}, {'content': 'x' * 1001}, {'content': 'x', 'schedule_open_hours': '{}'}):
            self.assertEqual(self.client.put(path, json=data, headers=self.headers('manager')).status_code, 400)
        self.assertEqual(self.client.get('/api/schedule/config', headers=self.headers()).json['schedule_reminder'], 'Saved')
