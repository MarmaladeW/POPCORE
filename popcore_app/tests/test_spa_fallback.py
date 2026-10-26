"""The app shell is served for page URLs, but a missing page file from an older build is a real 404.

Browsers reject HTML served for a script, so returning the shell for /assets/... breaks the page after a deploy.
Runs the real app.py in its own process with a throwaway database."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
PROBE = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from app import app
c = app.test_client()
out = {}
for path in ('/assets/Stock-old-build.js', '/stock?series_id=3', '/api/no-such-route'):
    r = c.get(path)
    out[path] = [r.status_code, r.content_type]
print(json.dumps(out))
'''


class SpaFallbackTests(unittest.TestCase):
    def test_missing_asset_is_404_while_pages_get_the_app_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, 'POPCORE_DB_PATH': str(Path(tmp) / 'probe.db'), 'DISABLE_SCHEDULER': '1',
                   'AUTH0_DOMAIN': 'probe.invalid', 'APP_ENV': 'development', 'SENTRY_DSN': ''}
            run = subprocess.run([sys.executable, '-c', PROBE, str(APP_DIR)], env=env, cwd=tmp,
                                 capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            result = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(result['/assets/Stock-old-build.js'][0], 404)
        self.assertNotIn('text/html', result['/assets/Stock-old-build.js'][1])
        if (APP_DIR / 'static' / 'index.html').exists():
            self.assertEqual(result['/stock?series_id=3'][0], 200)
        self.assertEqual(result['/api/no-such-route'][0], 404)


if __name__ == '__main__':
    unittest.main()
