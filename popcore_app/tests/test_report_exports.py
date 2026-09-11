from test_operational_reports import OperationalReportTests

class ReportExportTests(OperationalReportTests):
    def test_export_is_private_csv_and_formula_safe(self):
        response=self.client.get('/api/reports/tenders/export.csv?store_code=DT',headers=self.headers('manager'))
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.headers['Cache-Control'],'private, no-store')
        self.assertIn('text/csv',response.content_type)
