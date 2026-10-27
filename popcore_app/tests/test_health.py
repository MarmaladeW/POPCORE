"""GET /api/health answers without a login: 200 after a cheap database read, 503 when the
database cannot be read, and it exposes nothing but `ok` and the release."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import APP_DIR, IsolatedApiCase
import db
from blueprints import health

GIT = shutil.which('git')


def git(cwd, *args):
    return subprocess.run(
        ['git', '-c', 'user.name=Health Test', '-c', 'user.email=health@example.invalid',
         '-c', 'commit.gpgsign=false', '-c', 'init.defaultBranch=main', *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


class HealthRouteTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(health.bp)

    def get(self):
        return self.client.get('/api/health')  # no Authorization header

    def with_database(self, path):
        return patch.object(db, 'DB_PATH', str(path))

    def test_ok_without_login_and_nothing_else(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'ok': True, 'release': health.RELEASE})
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.client.head('/api/health').status_code, 200)

    def test_works_while_another_connection_holds_the_write_lock(self):
        writer = sqlite3.connect(db.DB_PATH)
        try:
            writer.execute('BEGIN IMMEDIATE')
            writer.execute("UPDATE stock SET notes='busy' WHERE product_id=?", (self.product_id,))
            self.assertEqual(self.get().status_code, 200)
        finally:
            writer.rollback()
            writer.close()

    def test_missing_database_is_503_and_is_not_created(self):
        missing = Path(self.tempdir.name) / 'missing.db'
        with self.with_database(missing):
            response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {'ok': False, 'release': health.RELEASE})
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertFalse(missing.exists())

    def test_corrupt_database_is_503_without_error_text(self):
        corrupt = Path(self.tempdir.name) / 'corrupt.db'
        corrupt.write_bytes(b'this is not an SQLite database' * 200)
        with self.with_database(corrupt), self.assertLogs(self.app.logger, 'WARNING'):
            response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {'ok': False, 'release': health.RELEASE})

    def test_database_without_the_schema_is_503(self):
        empty = Path(self.tempdir.name) / 'empty.db'
        sqlite3.connect(empty).close()
        with self.with_database(empty), self.assertLogs(self.app.logger, 'WARNING'):
            self.assertEqual(self.get().status_code, 503)


@unittest.skipIf(GIT is None, 'git is not installed')
class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temp_root = APP_DIR.parent / '.local' / 'tmp'
        temp_root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=temp_root))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def commit(self, repo, message):
        (repo / 'file.txt').write_text(message)
        git(repo, 'add', 'file.txt')
        git(repo, 'commit', '-q', '-m', message)
        return git(repo, 'rev-parse', 'HEAD')

    def test_this_checkout(self):
        self.assertEqual(health.git_commit(APP_DIR), git(APP_DIR, 'rev-parse', 'HEAD'))

    def test_branch_packed_refs_detached_head_and_worktree(self):
        repo = self.root / 'repo'
        repo.mkdir()
        git(repo, 'init', '-q')
        first = self.commit(repo, 'first')
        app_dir = repo / 'popcore_app'
        app_dir.mkdir()
        self.assertEqual(health.git_commit(app_dir), first)  # loose branch ref, from a subfolder
        second = self.commit(repo, 'second')
        git(repo, 'pack-refs', '--all')
        self.assertFalse((repo / '.git' / 'refs' / 'heads' / 'main').exists())
        self.assertEqual(health.git_commit(repo), second)  # packed ref
        git(repo, 'checkout', '-q', '--detach', first)
        self.assertEqual(health.git_commit(repo), first)  # detached HEAD
        git(repo, 'checkout', '-q', 'main')
        worktree = self.root / 'worktree'
        git(repo, 'worktree', 'add', '-q', '--detach', str(worktree), first)
        self.assertEqual(health.git_commit(worktree), first)  # linked worktree, detached
        git(worktree, 'checkout', '-q', '-b', 'candidate')
        third = self.commit(worktree, 'third')
        self.assertEqual(health.git_commit(worktree), third)  # worktree branch ref in the common dir
        self.assertEqual(health.git_commit(repo), second)

    def test_no_repository_falls_back_to_app_release(self):
        plain = self.root / 'plain'
        plain.mkdir()
        self.assertIsNone(health.git_commit(plain.resolve().anchor))  # the filesystem root has no .git
        with patch.object(health, 'git_commit', return_value=None), \
                patch.dict(os.environ, {'APP_RELEASE': 'build-7'}):
            self.assertEqual(health.detect_release(), 'build-7')
        with patch.object(health, 'git_commit', return_value=None), \
                patch.dict(os.environ, {'APP_RELEASE': ''}):
            self.assertIsNone(health.detect_release())


PROBE = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from app import app
client = app.test_client()
health = client.get('/api/health')
print(json.dumps({'health': [health.status_code, health.get_json()],
                  'stores': client.get('/api/stores').status_code}))
'''


class RealAppTests(unittest.TestCase):
    def test_registered_in_app_and_needs_no_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, 'POPCORE_DB_PATH': str(Path(tmp) / 'probe.db'), 'DISABLE_SCHEDULER': '1',
                   'AUTH0_DOMAIN': 'probe.invalid', 'APP_ENV': 'development', 'SENTRY_DSN': '',
                   'APP_RELEASE': '', 'POPCORE_HIDDEN_IMG_DIR': str(Path(tmp) / 'uploads' / 'hidden_imgs'),
                   'POPCORE_PAYMENT_EVIDENCE_DIR': str(Path(tmp) / 'uploads' / 'payment_evidence'),
                   'POPCORE_CONDITION_EVIDENCE_DIR': str(Path(tmp) / 'uploads' / 'condition_evidence')}
            env.pop('CLOVER_BACKGROUND_SYNC', None)
            run = subprocess.run([sys.executable, '-c', PROBE, str(APP_DIR)], env=env, cwd=tmp,
                                 capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            result = json.loads(run.stdout.strip().splitlines()[-1])
        status, body = result['health']
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {'ok', 'release'})
        self.assertIs(body['ok'], True)
        if GIT:
            self.assertEqual(body['release'], git(APP_DIR, 'rev-parse', 'HEAD'))
        self.assertEqual(result['stores'], 401)  # everything else still needs a login


if __name__ == '__main__':
    unittest.main()
