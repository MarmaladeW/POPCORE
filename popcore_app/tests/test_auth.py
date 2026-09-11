import base64
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask, jsonify
from jose import jwt as jose_jwt
from requests import RequestException


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

os.environ.setdefault('AUTH0_DOMAIN', 'test.invalid')

import auth


def _b64uint(value):
    raw = value.to_bytes((value.bit_length() + 7) // 8, 'big')
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


class TokenVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        numbers = cls.private_key.public_key().public_numbers()
        cls.jwk = {
            'kty': 'RSA',
            'kid': 'fixture-key',
            'use': 'sig',
            'alg': 'RS256',
            'n': _b64uint(numbers.n),
            'e': _b64uint(numbers.e),
        }

    def setUp(self):
        auth.AUTH0_DOMAIN = 'test.invalid'
        auth.AUTH0_AUDIENCE = 'https://popcore/api'
        auth._jwks_cache = None

    def token(self, **changes):
        now = int(time.time())
        claims = {
            'sub': 'auth0|fixture',
            'aud': auth.AUTH0_AUDIENCE,
            'iss': 'https://test.invalid/',
            'iat': now,
            'exp': now + 300,
        }
        claims.update(changes)
        return jose_jwt.encode(
            claims, self.private_key, algorithm='RS256',
            headers={'kid': 'fixture-key'},
        )

    def test_valid_token_returns_verified_claims(self):
        with patch.object(auth, '_get_jwks', return_value={'keys': [self.jwk]}):
            payload = auth._decode_token(self.token())
        self.assertEqual(payload['sub'], 'auth0|fixture')

    def test_wrong_issuer_is_rejected(self):
        with patch.object(auth, '_get_jwks', return_value={'keys': [self.jwk]}):
            with self.assertRaises(Exception):
                auth._decode_token(self.token(iss='https://other.invalid/'))

    def test_expiry_subject_and_audience_are_required(self):
        cases = (
            {'exp': int(time.time()) - 1},
            {'sub': None},
            {'aud': 'https://other.invalid/api'},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                token = self.token(**changes)
                with patch.object(
                    auth, '_get_jwks', return_value={'keys': [self.jwk]}
                ):
                    with self.assertRaises(Exception):
                        auth._decode_token(token)

    def test_unknown_key_refreshes_once(self):
        rotated = {'keys': [self.jwk]}
        with patch.object(
            auth, '_get_jwks', side_effect=[{'keys': []}, rotated]
        ) as get_jwks:
            payload = auth._decode_token(self.token())
        self.assertEqual(payload['sub'], 'auth0|fixture')
        self.assertEqual(get_jwks.call_count, 2)

    def test_unknown_key_is_rejected_after_one_refresh(self):
        with patch.object(
            auth, '_get_jwks', side_effect=[{'keys': []}, {'keys': []}]
        ) as get_jwks:
            with self.assertRaises(Exception):
                auth._decode_token(self.token())
        self.assertEqual(get_jwks.call_count, 2)

    def test_malformed_header_and_missing_kid_are_rejected(self):
        malformed = 'not-a-jwt'
        missing_kid = jose_jwt.encode(
            {'sub': 'fixture'}, self.private_key, algorithm='RS256'
        )
        for token in (malformed, missing_kid):
            with self.subTest(token=token):
                with self.assertRaises(Exception):
                    auth._decode_token(token)

    def test_wrong_algorithm_is_rejected(self):
        token = jose_jwt.encode(
            {'sub': 'fixture'}, 'test-secret-that-is-long-enough',
            algorithm='HS256', headers={'kid': 'fixture-key'},
        )
        with self.assertRaises(Exception):
            auth._decode_token(token)


class AuthenticationResponseTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)

        @app.get('/protected')
        @auth.login_required
        def protected():
            return jsonify({'ok': True})

        self.client = app.test_client()

    def test_jwks_outage_is_retriable_not_session_expired(self):
        with patch.object(
            auth, '_decode_token',
            side_effect=RequestException('provider secret details'),
        ):
            response = self.client.get(
                '/protected', headers={'Authorization': 'Bearer token'}
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json(),
            {
                'error': 'Authentication service unavailable',
                'code': 'authentication_unavailable',
            },
        )

    def test_invalid_token_remains_unauthorized(self):
        with patch.object(auth, '_decode_token', side_effect=ValueError('bad')):
            response = self.client.get(
                '/protected', headers={'Authorization': 'Bearer token'}
            )
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.get_json()['login_required'])


if __name__ == '__main__':
    unittest.main()
