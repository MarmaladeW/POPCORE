"""
app.py — POPCORE application factory.

Route implementations live in blueprints/. Shared DB and auth helpers
are in db.py and auth.py respectively.
"""
import os
import sentry_sdk
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from sentry_sdk.integrations.flask import FlaskIntegration

from db import (
    BASE_DIR, STATIC_DIR, HIDDEN_IMG_DIR,
    close_db, migrate_db,
)
from auth import AUTH0_DOMAIN

# ─── Sentry ───────────────────────────────────────────────────────────────────
sentry_sdk.init(
    dsn=os.environ.get('SENTRY_DSN', ''),
    integrations=[FlaskIntegration()],
    send_default_pii=True,
    traces_sample_rate=0.2,
    profiles_sample_rate=0.1,
    environment=os.environ.get('APP_ENV', 'development'),
    release=os.environ.get('APP_RELEASE', 'local'),
    server_name=os.environ.get('SERVER_NAME', 'localhost'),
)

# ─── Flask app ────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')

# ─── CORS ─────────────────────────────────────────────────────────────────────
_cors_env     = os.environ.get('CORS_ORIGINS', 'http://localhost:5173')
_cors_origins = [o.strip() for o in _cors_env.split(',') if o.strip()]
CORS(app, origins=_cors_origins, supports_credentials=False)

# ─── Startup guard ────────────────────────────────────────────────────────────
# Fail loudly if required Auth0 config is absent rather than silently accepting
# wrong-tenant tokens in production.
if not AUTH0_DOMAIN:
    raise RuntimeError(
        'AUTH0_DOMAIN environment variable is not set. '
        'Set it to your Auth0 tenant domain before starting the server.'
    )

os.makedirs(HIDDEN_IMG_DIR, exist_ok=True)

# ─── DB lifecycle ─────────────────────────────────────────────────────────────
app.teardown_appcontext(close_db)
migrate_db()

# ─── Blueprints ───────────────────────────────────────────────────────────────
from blueprints.users     import bp as users_bp
from blueprints.products  import bp as products_bp
from blueprints.stores    import bp as stores_bp
from blueprints.stock     import bp as stock_bp
from blueprints.sales     import bp as sales_bp
from blueprints.restock   import bp as restock_bp
from blueprints.inventory import bp as inventory_bp
from blueprints.schedule  import bp as schedule_bp
from blueprints.insights  import bp as insights_bp
from blueprints.settings  import bp as settings_bp

app.register_blueprint(users_bp)
app.register_blueprint(products_bp)
app.register_blueprint(stores_bp)
app.register_blueprint(stock_bp)
app.register_blueprint(sales_bp)
app.register_blueprint(restock_bp)
app.register_blueprint(inventory_bp)
app.register_blueprint(schedule_bp)
app.register_blueprint(insights_bp)
app.register_blueprint(settings_bp)

# ─── Nightly insight scheduler ────────────────────────────────────────────────
# Guard with DISABLE_SCHEDULER so tests and dev workers don't double-start it.
# With gunicorn preload_app=True this module runs once in the master process,
# so the scheduler thread is not duplicated across workers.
if not os.environ.get('DISABLE_SCHEDULER'):
    import atexit
    import sqlite3 as _sqlite3
    from datetime import datetime as _dt
    from apscheduler.schedulers.background import BackgroundScheduler
    from insights import generate_daily_insights as _gen_insights
    from db import DB_PATH as _DB_PATH

    _insights_last_run: dict = {'date': None}

    def _insights_job():
        """Run nightly insights at the time stored in app_settings.insight_generate_time."""
        now   = _dt.utcnow()
        today = now.date().isoformat()
        if _insights_last_run['date'] == today:
            return
        try:
            _con = _sqlite3.connect(_DB_PATH)
            _row = _con.execute(
                "SELECT value FROM app_settings WHERE key = 'insight_generate_time'"
            ).fetchone()
            _con.close()
            time_str = _row[0] if _row else '02:00'
        except Exception:
            time_str = '02:00'
        try:
            sched_h, sched_m = int(time_str.split(':')[0]), int(time_str.split(':')[1])
        except Exception:
            sched_h, sched_m = 2, 0
        if now.hour == sched_h and now.minute == sched_m:
            _gen_insights()
            _insights_last_run['date'] = today

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_insights_job, 'interval', minutes=1, id='nightly_insights')
    _scheduler.start()
    atexit.register(lambda: _scheduler.shutdown(wait=False))

# ─── SPA fallback (React Router) ─────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/') or request.path.startswith('/hidden_imgs/'):
        return jsonify({'error': 'Not found'}), 404
    return send_from_directory(STATIC_DIR, 'index.html')


if __name__ == '__main__':
    import socket
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = '查询失败'
    print('Starting POPCORE Inventory System')
    print(f'  本机访问:  http://localhost:5000')
    print(f'  手机访问:  http://{local_ip}:5000')
    print(f'  (手机需连接同一WiFi)')
    app.run(debug=False, host='0.0.0.0', port=5000)
