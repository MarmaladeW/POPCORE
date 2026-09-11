import os
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from support import IsolatedApiCase


class PrivateInputTests(IsolatedApiCase):
    def test_report_parser_defaults_to_local_rules(self):
        with patch('llm_parser.available', return_value=True), patch(
            'llm_parser.parse_report_llm',
            side_effect=AssertionError('raw report left the process'),
        ) as parse_llm:
            response = self.client.post(
                '/api/sales/parse_report',
                headers=self.headers(),
                json={'store_code': 'DT', 'text': '2026-09-08 DT'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['parser_engine'], 'rules')
        parse_llm.assert_not_called()

    def test_llm_requires_deployment_opt_in_and_explicit_request(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'canary'}, clear=False):
            os.environ.pop('ENABLE_LLM_PARSER', None)
            with patch(
                'llm_parser.parse_report_llm',
                side_effect=AssertionError('raw report left the process'),
            ) as parse_llm:
                response = self.client.post(
                    '/api/sales/parse_report',
                    headers=self.headers(),
                    json={
                        'store_code': 'DT',
                        'text': '2026-09-08 DT',
                        'engine': 'llm',
                    },
                )

        self.assertEqual(response.status_code, 200)
        parse_llm.assert_not_called()

    def test_enabled_llm_failures_fall_back_to_rules(self):
        cases = (
            ('no API key', False, None),
            ('timeout', True, TimeoutError('synthetic timeout')),
            ('no usable output', True, None),
        )
        for label, available, result in cases:
            with self.subTest(label=label), patch.dict(
                os.environ, {'ENABLE_LLM_PARSER': '1'}, clear=False
            ), patch('llm_parser.available', return_value=available), patch(
                'llm_parser.parse_report_llm',
                side_effect=result if isinstance(result, Exception) else None,
                return_value=result if not isinstance(result, Exception) else None,
            ):
                response = self.client.post(
                    '/api/sales/parse_report',
                    headers=self.headers(),
                    json={
                        'store_code': 'DT',
                        'text': '2026-09-08 DT',
                        'engine': 'llm',
                    },
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['parser_engine'], 'rules')

    def test_invalid_llm_schema_produces_no_usable_output(self):
        import llm_parser

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    'content': [{
                        'type': 'tool_use',
                        'name': 'record_daily_report',
                        'input': {'items': [{'section': 'pos', 'name': ''}]},
                    }]
                }

        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'canary'}), patch(
            'llm_parser.requests.post', return_value=Response()
        ) as request_post:
            result = llm_parser.parse_report_llm('private report text')

        self.assertIsNone(result)
        request_post.assert_called_once()


class TelemetryPrivacyTests(unittest.TestCase):
    def test_sensitive_request_data_is_removed(self):
        from telemetry import scrub_event

        canary = 'secret-canary-value'
        event = {
            'user': {'email': canary, 'id': canary},
            'request': {
                'headers': {
                    'Authorization': f'Bearer {canary}',
                    'Cookie': f'session={canary}',
                    'Accept': 'application/json',
                },
                'query_string': f'token={canary}',
                'data': {'report': canary},
            },
            'breadcrumbs': {
                'values': [{
                    'category': 'httplib',
                    'data': {'url': f'https://example.invalid/?key={canary}'},
                }],
            },
        }

        scrubbed = scrub_event(event, {})

        self.assertNotIn(canary, json.dumps(scrubbed))
        self.assertEqual(
            scrubbed['request']['headers'], {'Accept': 'application/json'}
        )

if __name__ == '__main__':
    unittest.main()
