# Production template readiness

The templates use `/var/lib/popcore` for the database and private uploads, `/var/log/popcore` for logs and `/var/cache/popcore` for cache. systemd creates these directories for the configured service user with a restrictive umask. Gunicorn preloads the app so one master process owns the in-process daily scheduler; the durable `(job, business_date)` claim also prevents duplicate runs and permits retry after failure.

nginx permits request bodies up to 12 MB, matching the application's 10 MB image payload plus multipart overhead. Direct upload, payment-evidence and condition-evidence aliases return 404. Security headers are repeated for cached assets because nginx child locations do not inherit parent `add_header` values. CSP is report-only; replace `YOUR_DOMAIN` and `YOUR_AUTH0_DOMAIN`, observe reports with the real tenant and asset paths, then separately approve enforcement.

The insight job uses America/Toronto business dates, runs once after the configured time even after a restart, records durable success or failure, and retries failed dates. Legacy quantity-times-price insight checks do not run after authoritative inventory activation and are not included in Today.

This Windows host does not provide Bash, nginx, `systemd-analyze`, or an accessible WSL distribution. Run these on the isolated Linux candidate host or CI before deployment:

```sh
bash -n setup_production.sh popcore_app/backup.sh
nginx -t -c /absolute/candidate/popcore_app/nginx.conf
systemd-analyze verify /absolute/candidate/popcore_app/popcore.service
```

The live owner must also verify the real Auth0 callbacks/origins, CSP reports, TLS headers on success and error responses, filesystem ownership, log rotation, off-host backup credentials and recovery, scanner/camera behavior, and active service settings. Do not run `setup_production.sh` until those substitutions and checks are approved.
