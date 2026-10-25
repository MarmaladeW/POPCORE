"""An Auth0 account without a POPCORE role (e.g. a public sign-up) gets no access at all."""
from support import IsolatedApiCase


class RolelessAccountTests(IsolatedApiCase):
    def test_roleless_account_is_refused_everywhere(self):
        for path in ('/api/stores', '/api/product_types', '/api/products/search?q=a'):
            response = self.client.get(path, headers=self.headers('norole'))
            self.assertEqual((response.status_code, response.get_json()['code']), (403, 'no_role'), path)

    def test_accounts_with_a_role_still_get_in(self):
        for role in ('viewer', 'staff'):
            self.assertEqual(self.client.get('/api/stores', headers=self.headers(role)).status_code, 200, role)
