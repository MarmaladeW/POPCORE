# Gunicorn settings for POPCORE staging, installed as /etc/popcore/gunicorn-staging.conf.py.
#
# Passing this file with -c keeps gunicorn from loading the checkout's own
# gunicorn.conf.py, which binds production's port. One worker on loopback only; nginx
# proxies the staging hostname here. No preload: the scheduler is off on staging, and
# loading the app in the worker keeps the master process small.
workers = 1
bind = '127.0.0.1:5002'
timeout = 120
preload_app = False
accesslog = '-'
errorlog = '-'
# Recycle the worker now and then so a long-running staging session stays small.
max_requests = 500
max_requests_jitter = 50
