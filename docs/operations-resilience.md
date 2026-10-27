# Operations resilience

How POPCORE production on the droplet (1 vCPU, 1 GB RAM + 2 GB swap) is kept safe from bad releases, lost servers, mistaken edits and unnoticed outages, at almost no cost. Everything here runs on the existing droplet or on free tiers. Nothing in this document is installed yet: each server step needs the owner's go-ahead.

## What protects against what

| Failure | Protection | What you do | Cost a month |
|---|---|---|---|
| A release crashes or fails its health check | `deploy_production.sh` rehearses the release on a copy of live data, then restarts and polls `/api/health`; on failure it puts the previous code back by itself | Read the output | $0 |
| A migration fails on real data | The rehearsal runs the release's migrations on a copy first and stops before production changes | Fix the release | $0 |
| A migration succeeds but breaks the old code after a rollback | Every deploy keeps a pre-deploy backup; `rollback_production.sh --restore-db` puts it back (writes made after it are lost, and kept aside) | Decide whether to restore | $0 |
| The droplet dies or its disk is corrupted | Litestream copies every database change off-site within about 10 s; DigitalOcean weekly backups keep the whole server; the nightly backup reaches the Mac | Restore on a new or restored droplet | $0 (R2 or B2 free tier) + about $1.20 (DO weekly backups) |
| Someone deletes or changes data by mistake | Litestream point-in-time restore for 30 days; nightly backups (30 days on the server, 60 on the Mac); pre-deploy backups | Restore a copy from before the mistake and copy rows back | $0 |
| An outage nobody notices | A free uptime monitor on `/api/health` sends an email or phone push; an optional Litestream heartbeat warns when off-site copying stops | Act on the alert | $0 |
| A release needs testing on real data first | Staging on the same droplet, started only when needed | `refresh_staging.sh`, then test | $0 |

Uploads (product images, payment and condition evidence) are not in Litestream. They are in the nightly backup, so after a server loss they can be up to a day old.

## Health check

`GET /api/health` needs no login. It reads the database once and answers `200 {"ok": true, "release": "<commit>"}`, or `503 {"ok": false, ...}` when the database cannot be read. `release` is the git commit the app started from (else `APP_RELEASE`). It exposes nothing else. Through nginx it is `https://popcore.store/api/health`.

## Deploying

The scripts live in `scripts/`. Install copies once, so they keep working after a rollback to a commit that predates them:

```sh
install -m 755 /root/POPCORE/scripts/deploy_production.sh /usr/local/sbin/popcore-deploy
install -m 755 /root/POPCORE/scripts/rollback_production.sh /usr/local/sbin/popcore-rollback
install -m 755 /root/POPCORE/scripts/refresh_staging.sh /usr/local/sbin/popcore-staging-refresh
install -m 755 /root/POPCORE/scripts/restore_from_litestream.sh /usr/local/sbin/popcore-restore
```

Repeat the `install` lines when the scripts change. Then, as root:

```sh
popcore-deploy --rehearse-only     # optional dry run: backup + rehearsal, production untouched
popcore-deploy                     # deploy origin/main
popcore-deploy <commit>            # or a specific commit that fast-forwards main
```

The script:

1. Refuses unless `/root/POPCORE` is on `main`, tracked files are unchanged (your untracked files are ignored and left alone), and the commit fast-forwards. Another deploy or rollback holds a lock.
2. Takes an online backup of the live database with SQLite's backup API (staff keep working), checks it with `integrity_check` and keeps it as `/root/popcore-backups/pre-deploy-<UTC time>-<commit>.db`.
3. Checks the release out in a temporary git worktree and imports its app against a copy of that backup, with the scheduler off and no secrets. This runs the release's migrations on real data. An import error, a failed migration, a failed `integrity_check` or new foreign-key violations stop the deploy here. It prints the schema changes and how long the start took (about the downtime of the restart).
4. Fast-forwards `/root/POPCORE` and restarts `popcore`.
5. Waits up to 90 s for `/api/health` to report the new commit twice and for the app shell to load. If it does not, it resets the code to the previous commit, restarts, and prints whether the database needs attention.

Exit codes: 0 deployed and healthy; 1 refused or stopped before production changed; 2 the release failed and the previous code is back and healthy; 3 production is not healthy, act now.

The database is never restored automatically. Once the new release has started it may have migrated the database and taken real sales, counts or shifts. Putting the backup back would silently drop them. Most failed releases fail before they write anything, and the previous code runs on the current database as before. If the release changed the schema and the previous code misbehaves, the script prints the exact `popcore-rollback --restore-db ...` command, and restoring is your decision.

When `requirements.txt` changes, the script prints the `pip install` command to run first; the rehearsal stops if the venv lacks a package. Every deploy and rollback is logged in `/root/popcore-backups/deploy-history.log`. Pre-deploy backups are about 5 MB each and are not deleted automatically.

## Rolling back

```sh
popcore-rollback                                  # back to the commit before the last healthy deploy
popcore-rollback <commit>                         # back to an earlier commit
popcore-rollback --restore-db <backup.db> <commit>  # also put a database backup back
```

It refuses unless the target is an earlier commit of the live one, lists the migrations in the live database that the target does not know, resets the code with `git reset --keep`, restarts and waits for health. Releases older than `/api/health` count as up when `/api/stores` answers 401.

With `--restore-db` it first copies the backup next to the live database, stops `popcore` and Litestream, saves the current database files to `/root/popcore-backups/replaced-<UTC time>/`, swaps the backup in, gives Litestream fresh local state, and starts everything again. If it stops early it starts the services it stopped.

## Off-site database copy (Litestream)

Litestream streams every change of `/root/POPCORE/popcore_app/popcore.db` to an S3-compatible bucket at most every 10 s, takes a full snapshot daily and keeps 30 days, so any moment of the last month can be restored. It uses about 20–60 MB of RAM.

Install (needs the bucket from the owner actions below; use Litestream 0.5.14 or newer, see the [releases](https://github.com/benbjohnson/litestream/releases)):

```sh
uname -m                                   # x86_64 on a regular droplet
cd /root && wget https://github.com/benbjohnson/litestream/releases/download/v0.5.14/litestream-0.5.14-linux-x86_64.deb
dpkg -i litestream-0.5.14-linux-x86_64.deb
install -m 600 /root/POPCORE/deployment/litestream.env.example /etc/litestream.env    # fill in the bucket and key
install -m 644 /root/POPCORE/deployment/litestream.yml /etc/litestream.yml
install -m 644 /root/POPCORE/deployment/litestream.service /etc/systemd/system/litestream.service
systemctl daemon-reload && systemctl enable litestream && systemctl restart litestream
journalctl -u litestream -n 30             # no errors; the first snapshot is uploaded
popcore-restore                            # proves the copy: restores into a new file and checks it
```

`popcore-restore [-t 2026-10-27T15:00:00Z] [-o file.db]` restores into a new file under `/root/popcore-restore/` (never over the live database), then runs `integrity_check` and `foreign_key_check` and prints each table's row count with its newest `created_at`.

Compatibility with POPCORE, checked in the code:

- Every connection uses WAL mode, which Litestream requires. Nothing runs `wal_checkpoint(TRUNCATE)`, `VACUUM` or changes the journal mode.
- Busy timeouts are at least the 5 s Litestream recommends: 15 s for requests (`db.get_db`), 5 s for migrations and inventory posting, 10 s for Clover sync.
- The nightly backup, the pre-deploy backup and the staging refresh only read the live database through the backup API. `VACUUM INTO` another file would also be fine; a plain `VACUUM` of the live file would resend the whole database. The server's `/usr/local/sbin/popcore-nightly-backup` is not in this repository: check it uses neither before enabling Litestream.
- Litestream keeps a `.popcore.db-litestream` folder next to the database. Git ignores it (`*.db-*`), so deploys still see a clean tree.
- Never copy a file over the live database while Litestream runs. `popcore-rollback --restore-db` handles this.
- Only production replicates to `popcore/production`. Never point staging or another server at it, and never let a server restored from an older image start Litestream before its database is current (see "The droplet is lost").

## Staging

Staging runs on the droplet as a separate checkout (`/opt/popcore-staging`) with one gunicorn worker on `127.0.0.1:5002`, its own data in `/var/lib/popcore-staging`, the scheduler and Clover sync off, and no Clover or Auth0 user-management credentials. It runs as an unprivileged user that cannot read `/root`. It uses about 100–150 MB of RAM while running (capped at 400 MB, and the kernel kills it before production), so start it only when you need it. It stops itself after 12 hours.

One-time setup, as root (clone the repository the same way `/root/POPCORE` was cloned):

```sh
useradd --system --home-dir /var/lib/popcore-staging --shell /usr/sbin/nologin popcore-staging
git clone https://github.com/MarmaladeW/POPCORE.git /opt/popcore-staging
install -d -m 755 /etc/popcore
install -m 600 /root/POPCORE/deployment/popcore-staging.env.example /etc/popcore/staging.env   # fill in Auth0 + CORS
install -m 644 /root/POPCORE/deployment/gunicorn-staging.conf.py /etc/popcore/gunicorn-staging.conf.py
install -m 644 /root/POPCORE/deployment/popcore-staging.service /etc/systemd/system/popcore-staging.service
systemctl daemon-reload                     # do not enable it
cp /root/POPCORE/deployment/nginx-staging.conf /etc/nginx/sites-available/popcore-staging
sed -i 's/STAGING_HOSTNAME/staging.popcore.store/g' /etc/nginx/sites-available/popcore-staging
ln -s /etc/nginx/sites-available/popcore-staging /etc/nginx/sites-enabled/popcore-staging
nginx -t && systemctl reload nginx
certbot --nginx -d staging.popcore.store
```

Use:

```sh
popcore-staging-refresh --commit origin/<branch> --start   # that code on a fresh copy of live data
popcore-staging-refresh --source nightly                    # last night's data instead
systemctl stop popcore-staging                              # when you are done
```

Then open `https://staging.popcore.store`. Staging uses production's Auth0 login and a copy of real data, so treat it as private as production. The user-management page does not work there, product-match ranking does not learn from corrections, and older product images kept outside the app's upload folder are missing.

## Uptime alerts

Add an HTTP monitor for `https://popcore.store/api/health` every 5 minutes that alerts when the status is not 200 or the body lacks `"ok":true`, with email and the provider's phone-app push. SMS costs money with every provider. UptimeRobot is the usual choice, but reports say its free plan has been limited to non-commercial use since December 2024 (its paid plan is about $8–10 a month), so read its terms. Better Stack's free tier (10 monitors, 3-minute checks, email alerts) is an alternative. For the optional Litestream heartbeat, create a free healthchecks.io check with a 10-minute period and 1-hour grace and put its URL in `LITESTREAM_HEARTBEAT_URL`.

## Owner actions (one time)

1. Create a private Cloudflare R2 bucket (Cloudflare asks for a payment method to turn on R2, even on the free tier) or a private Backblaze B2 bucket, and an API key limited to that bucket. Put both in `/etc/litestream.env`.
2. Turn on weekly backups for the droplet in the DigitalOcean control panel (Droplet, then Backups). They cost 20% of the droplet's price, about $1.20 a month on a $6 droplet.
3. Approve installing Litestream and the deploy scripts on the droplet (sections above).
4. For staging: a DNS A record `staging.popcore.store` pointing at the droplet, and in Auth0 add `https://staging.popcore.store` to the SPA application's Allowed Callback URLs, Allowed Logout URLs and Allowed Web Origins.
5. Create the uptime monitor and, optionally, the healthchecks.io check.

## When something goes wrong

**After a deploy, the site misbehaves but health is ok.** Run `popcore-rollback`. If it lists unknown migrations and the old code still misbehaves, run the `--restore-db` command it prints.

**The monitor says the site is down.** On the droplet console: `systemctl status popcore`, `journalctl -u popcore -n 100`, `curl -s localhost:5000/api/health`, `df -h`, `free -m`. If it started after a deploy, `popcore-rollback`.

**Data was changed by mistake.** Restore a copy from just before the mistake into a new file, for example `popcore-restore -t 2026-10-27T14:00:00Z`, check it on staging (`popcore-staging-refresh --source /root/popcore-restore/<file>.db --start`), then copy back only the affected rows with `sqlite3` (`ATTACH` the copy). Replace the whole database (`popcore-rollback --restore-db <file> <live commit>`) only when the damage is widespread, accepting the loss of later writes.

**The droplet is lost.**

1. Note the UTC time the trouble started.
2. Create a droplet from the latest DigitalOcean backup. On its console, first run `systemctl disable --now litestream popcore`. A restored image holds a database up to a week old, and its Litestream would otherwise start sending that old state into the off-site history. (On a brand-new droplet set up from the README instead, install Litestream and its config files as above but do not enable or start it yet.)
3. `popcore-restore -t <a minute before the trouble started>` and check the output. The time limit keeps out anything the restored image may have sent.
4. `popcore-rollback --restore-db <that file> $(git -C /root/POPCORE rev-parse HEAD)` puts it in place and starts `popcore` (Litestream stays off). On a brand-new server with no database yet, copy the file to `/root/POPCORE/popcore_app/popcore.db` with mode 600 before the first start instead.
5. In `/etc/litestream.yml` change `path: popcore/production` to a new path such as `popcore/production-2026-11`, then `systemctl enable popcore` and `systemctl enable --now litestream`. The old path stays untouched as the record up to the outage.
6. Restore the uploads from the newest nightly archive on the Mac (`~/Backups/popcore/nightly/`): it holds `uploads/` and `legacy_static_hidden_imgs/`.
7. Point DNS at the droplet if its address changed, and check `https://popcore.store/api/health`.

## Quarterly restore drill (about 30 minutes)

1. `popcore-restore`: `integrity_check` is ok, row counts look right, and the newest `created_at` values are minutes old.
2. `popcore-restore -t <yesterday at noon, UTC>`: an older point in time also restores.
3. `popcore-staging-refresh --source <the restored file> --start`, log in on staging, open Today, Stock and Sales, then `systemctl stop popcore-staging`.
4. `popcore-deploy --rehearse-only`: the rehearsal passes on today's data.
5. Check that the Mac received last night's archive (`~/Backups/popcore/nightly/`) and that the DigitalOcean control panel lists recent droplet backups.
6. Delete the restored files in `/root/popcore-restore/` and old pre-deploy backups you no longer need, and note the date of the drill.

## Monthly cost

| Item | Cost |
|---|---|
| Health check, deploy and rollback scripts, staging | $0 |
| Litestream to R2 or B2 (database about 5 MB; well under 1 GB stored; at most one upload every 10 s) | $0 within the free tiers |
| DigitalOcean weekly backups | 20% of the droplet price (about $1.20) |
| Uptime monitor and heartbeat check | $0 on free plans; SMS is extra |
| Staging DNS record and TLS certificate | $0 |
