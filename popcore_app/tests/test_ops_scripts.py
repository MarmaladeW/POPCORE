"""deploy_production.sh and rollback_production.sh against a throwaway production.

The scripts run for real: git fetch, fast-forward and reset, the online backup, the
rehearsal on a copy, health polling, the deploy history and the installed commands. Two
things are stand-ins: `id -u` answers 0, and `systemctl` records each call and, when
popcore starts, imports the checked-out release the way gunicorn does, so its
migrations run on the live database. A small HTTP server plays the app: /api/health
answers as the commit checked out, and 503 while that commit carries an UNHEALTHY file.
"""
import http.server
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts'
COMMANDS = {
    'deploy_production.sh': 'popcore-deploy',
    'rollback_production.sh': 'popcore-rollback',
    'refresh_staging.sh': 'popcore-staging-refresh',
    'restore_from_litestream.sh': 'popcore-restore',
}
GIT_ENV = {
    **os.environ,
    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
    'GIT_AUTHOR_NAME': 'Ops Test', 'GIT_AUTHOR_EMAIL': 'ops@example.invalid',
    'GIT_COMMITTER_NAME': 'Ops Test', 'GIT_COMMITTER_EMAIL': 'ops@example.invalid',
}

# A release in miniature: importing app runs its migrations, one table each.
APP = '''import os
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
if (HERE / 'BROKEN').exists():
    raise RuntimeError('this release cannot start')
con = sqlite3.connect(os.environ['POPCORE_DB_PATH'])
con.execute('CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY)')
for name in (HERE / 'migrations.txt').read_text().split():
    con.execute(f'CREATE TABLE IF NOT EXISTS {name} (id INTEGER PRIMARY KEY)')
    con.execute('INSERT OR IGNORE INTO _migrations VALUES (?)', (name,))
con.commit()
con.close()
'''

SYSTEMCTL = '''#!/bin/sh
printf '%s\\n' "$*" >>"$FAKE_SYSTEMCTL_LOG"
case "$1" in
  start|restart)
    if [ "$2" = popcore ]; then
      (cd "$POPCORE_REPO_DIR/popcore_app" && "$POPCORE_VENV/bin/python" -c 'import app') >/dev/null 2>&1 || true
    fi ;;
  is-active|is-failed) exit 1 ;;
esac
exit 0
'''

ID = '''#!/bin/sh
if [ "$1" = -u ]; then echo 0; else exec /usr/bin/id "$@"; fi
'''


def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, env=GIT_ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


def executable(path, text):
    path.write_text(text)
    path.chmod(0o755)


def script(name, extra=''):
    return (SCRIPTS / name).read_text() + extra


def migrations(path):
    with closing(sqlite3.connect(path)) as con:
        return [row[0] for row in con.execute('SELECT name FROM _migrations ORDER BY name')]


class FakeApp(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        repo = self.server.repo
        if self.path == '/api/health':
            ok = not (repo / 'popcore_app' / 'UNHEALTHY').exists()
            body = json.dumps({'ok': ok, 'release': git(repo, 'rev-parse', 'HEAD')})
            self.answer(200 if ok else 503, 'application/json', body)
        elif self.path == '/':
            self.answer(200, 'text/html; charset=utf-8', '<!doctype html><title>POPCORE</title>')
        else:
            self.answer(404, 'text/plain', 'not found')

    def answer(self, status, kind, body):
        data = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@unittest.skipIf(os.name == 'nt' or not shutil.which('bash') or not shutil.which('git'),
                 'the operations scripts run in bash on the Linux droplet')
class OpsScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='popcore-ops-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dev = self.tmp / 'dev'
        self.repo = self.tmp / 'POPCORE'  # production's checkout
        self.db = self.repo / 'popcore_app' / 'popcore.db'
        self.sbin = self.tmp / 'sbin'
        self.backups = self.tmp / 'backups'
        tools = self.tmp / 'bin'
        venv = self.tmp / 'venv' / 'bin'
        for folder in (self.sbin, tools, venv):
            folder.mkdir(parents=True)
        (venv / 'python').symlink_to(sys.executable)
        executable(tools / 'systemctl', SYSTEMCTL)
        executable(tools / 'id', ID)

        origin = self.tmp / 'origin.git'
        git(self.tmp, 'init', '--quiet', '--bare', str(origin))
        git(self.tmp, 'clone', '--quiet', str(origin), str(self.dev))
        git(self.dev, 'symbolic-ref', 'HEAD', 'refs/heads/main')
        self.v1 = self.release('first release', ['m1'])
        git(self.tmp, 'clone', '--quiet', '--branch', 'main', str(origin), str(self.repo))

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakeApp)
        server.repo = self.repo
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        self.env = {
            **GIT_ENV,
            'PATH': f'{tools}{os.pathsep}{os.environ["PATH"]}',
            'POPCORE_REPO_DIR': str(self.repo),
            'POPCORE_DB_PATH': str(self.db),
            'POPCORE_VENV': str(self.tmp / 'venv'),
            'POPCORE_BACKUP_DIR': str(self.backups),
            'POPCORE_SBIN_DIR': str(self.sbin),
            'POPCORE_LOCK_FILE': str(self.tmp / 'deploy.lock'),
            'POPCORE_HEALTH_URL': f'http://127.0.0.1:{server.server_address[1]}/api/health',
            'HEALTH_TIMEOUT': '6',
            'FAKE_SYSTEMCTL_LOG': str(self.tmp / 'systemctl.log'),
        }
        # Production runs the first release, and popcore-deploy was installed by hand.
        subprocess.run([str(tools / 'systemctl'), 'start', 'popcore'], env=self.env, check=True)
        (self.tmp / 'systemctl.log').unlink()
        executable(self.sbin / 'popcore-deploy', script('deploy_production.sh'))

    def release(self, message, names, scripts=None, files=None):
        """Commit a release to origin/main from a developer's clone; returns its sha."""
        app_dir = self.dev / 'popcore_app'
        for folder in (app_dir, self.dev / 'scripts'):
            folder.mkdir(exist_ok=True)
        (app_dir / 'app.py').write_text(APP)
        (app_dir / 'migrations.txt').write_text('\n'.join(names) + '\n')
        (app_dir / 'db.py').write_text(f'MIGRATIONS = {names!r}\n')  # what the rollback reads
        for name in COMMANDS:
            (self.dev / 'scripts' / name).write_text((scripts or {}).get(name) or script(name))
        for name, text in (files or {}).items():
            (app_dir / name).write_text(text)
        git(self.dev, 'add', '-A')
        git(self.dev, 'commit', '--quiet', '-m', message)
        git(self.dev, 'push', '--quiet', 'origin', 'HEAD:main')
        return git(self.dev, 'rev-parse', 'HEAD')

    def run_command(self, name, *args):
        result = subprocess.run([str(self.sbin / name), *args], env=self.env,
                                capture_output=True, text=True, timeout=180)
        return result.returncode, result.stdout + result.stderr

    def head(self):
        return git(self.repo, 'rev-parse', 'HEAD')

    def calls(self):
        """The systemctl calls that start or stop something, in order."""
        log = self.tmp / 'systemctl.log'
        lines = log.read_text().splitlines() if log.exists() else []
        return [line for line in lines if line.split()[0] in ('start', 'stop', 'restart')]

    def history(self):
        return (self.backups / 'deploy-history.log').read_text().splitlines()

    def installed(self):
        return {path.name: path.read_text() for path in sorted(self.sbin.iterdir())}

    def test_a_healthy_deploy_installs_the_release_commands(self):
        rollback = script('rollback_production.sh', '\n# changed in release 2\n')
        v2 = self.release('second release', ['m1', 'm2'], scripts={
            'rollback_production.sh': rollback,
            'refresh_staging.sh': script('refresh_staging.sh', '\nif then\n'),  # a syntax error
        })

        code, out = self.run_command('popcore-deploy')

        self.assertEqual(code, 0, out)
        self.assertEqual(self.head(), v2)
        self.assertEqual(migrations(self.db), ['m1', 'm2'])
        self.assertEqual(self.calls(), ['restart popcore'])
        self.assertTrue(self.history()[-1].endswith('result=healthy'), self.history())
        self.assertEqual(len(list(self.backups.glob('pre-deploy-*.db'))), 1)
        self.assertEqual(self.installed(), {
            'popcore-deploy': script('deploy_production.sh'),
            'popcore-restore': script('restore_from_litestream.sh'),
            'popcore-rollback': rollback,
        })
        self.assertTrue(all(os.access(path, os.X_OK) for path in self.sbin.iterdir()))
        self.assertIn('fails bash -n; popcore-staging-refresh stays as it was', out)
        self.assertEqual(list((self.backups / 'tmp').iterdir()), [])  # the rehearsal's copy is gone

    def test_a_release_that_cannot_start_stops_before_production_changes(self):
        self.release('broken release', ['m1', 'm2'], files={'BROKEN': ''})

        code, out = self.run_command('popcore-deploy')

        self.assertEqual(code, 1, out)
        self.assertIn('failed to start on a copy of the live data', out)
        self.assertEqual(self.head(), self.v1)
        self.assertEqual(migrations(self.db), ['m1'])
        self.assertEqual(self.calls(), [])
        self.assertEqual(list(self.installed()), ['popcore-deploy'])

    def test_an_unhealthy_release_is_rolled_back_and_installs_nothing(self):
        self.release('unhealthy release', ['m1', 'm2'], files={'UNHEALTHY': ''},
                     scripts={'rollback_production.sh': script('rollback_production.sh', '\n# release 2\n')})

        code, out = self.run_command('popcore-deploy')

        self.assertEqual(code, 2, out)
        self.assertIn('rolling the code back', out)
        self.assertEqual(self.head(), self.v1)
        self.assertEqual(self.calls(), ['restart popcore', 'restart popcore'])
        self.assertTrue(self.history()[-1].endswith('result=rolled-back'), self.history())
        # Its migrations ran during the failed start, so the restore command is offered.
        self.assertEqual(migrations(self.db), ['m1', 'm2'])
        self.assertIn('--restore-db', out)
        self.assertEqual(list(self.installed()), ['popcore-deploy'])

    def test_rehearse_only_leaves_production_alone(self):
        self.release('second release', ['m1', 'm2'],
                     scripts={'rollback_production.sh': script('rollback_production.sh', '\n# release 2\n')})

        code, out = self.run_command('popcore-deploy', '--rehearse-only')

        self.assertEqual(code, 0, out)
        self.assertIn('new migrations: m2', out)
        self.assertEqual(self.head(), self.v1)
        self.assertEqual(migrations(self.db), ['m1'])
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.backups / 'deploy-history.log').exists())
        self.assertEqual(list(self.installed()), ['popcore-deploy'])

    def test_running_it_on_the_live_commit_brings_the_commands_in_line(self):
        executable(self.sbin / 'popcore-deploy', script('deploy_production.sh', '\n# an older copy\n'))

        code, out = self.run_command('popcore-deploy')

        self.assertEqual(code, 0, out)
        self.assertIn('already at this commit', out)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.installed(), {command: script(name) for name, command in COMMANDS.items()})

    def test_rollback_returns_to_the_previous_release_and_keeps_the_commands(self):
        self.release('second release', ['m1', 'm2'],
                     scripts={'rollback_production.sh': script('rollback_production.sh', '\n# release 2\n')})
        self.assertEqual(self.run_command('popcore-deploy')[0], 0)
        commands = self.installed()
        backup = next(self.backups.glob('pre-deploy-*.db'))

        code, out = self.run_command('popcore-rollback')

        self.assertEqual(code, 0, out)
        self.assertEqual(self.head(), self.v1)
        self.assertIn('[deploy-history]', out)
        self.assertIn('migrations from a newer release', out)
        self.assertIn(f'--restore-db {backup} {self.v1}', out)
        self.assertEqual(migrations(self.db), ['m1', 'm2'])  # only the code went back
        self.assertEqual(self.installed(), commands)
        self.assertTrue(self.history()[-1].endswith('result=healthy'), self.history())
        self.assertIn('\trollback\t', self.history()[-1])

    def test_rollback_with_a_backup_restores_it_and_keeps_the_replaced_database(self):
        self.release('second release', ['m1', 'm2'])
        self.assertEqual(self.run_command('popcore-deploy')[0], 0)
        backup = next(self.backups.glob('pre-deploy-*.db'))

        code, out = self.run_command('popcore-rollback', '--restore-db', str(backup))

        self.assertEqual(code, 0, out)
        self.assertEqual(self.head(), self.v1)
        self.assertEqual(migrations(self.db), ['m1'])
        saved = list(self.backups.glob('replaced-*/popcore.db'))
        self.assertEqual(len(saved), 1, out)
        self.assertEqual(migrations(saved[0]), ['m1', 'm2'])
        self.assertEqual(self.calls(), ['restart popcore', 'stop popcore', 'start popcore'])
        self.assertFalse(Path(f'{self.db}.restoring').exists())


if __name__ == '__main__':
    unittest.main()
