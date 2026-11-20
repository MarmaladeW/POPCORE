# 可疑人员 Watch list

Photos and notes of people who stole from the stores, so staff recognise them when they come back. Owner, 2026-11-19:
"in the 损耗 section, there can be a section where I can upload images of stealers, so everyone can recognize the
people that stole from the store, we can write own information about the stealer, and upload image of them, and we
can mark if it is resolved or not, the stealer information also something showing in the store computer."

Code: `popcore_app/watch_list.py`, routes in `blueprints/watch_list.py`, page `frontend/src/pages/WatchList/`,
migration `watch_list` in `db.py`.

## Where it is

- `/watch-list`, 可疑人员 · Watch list, in the 日常 menu (rail and 更多) for every role.
- The 损耗 Loss page links to it at the top, with how many records are open.

## A record

- One or more photos (at most 12).
- 情况 Information: free text, such as looks, clothes, what was taken and when.
- 门店 Store: DT, MK or not given.
- 见到日期 Date seen: today or earlier.
- 未解决 Open or 已解决 Resolved, with when and by whom it was resolved, and an optional note.
- Who added it and when, and who last changed it.

Open records come first, newest date seen first. Resolved ones sit under 已解决 Resolved, folded shut.

## Who can do what

| | Read the list and photos | Add, edit, add or remove photos, resolve, reopen, delete |
| --- | --- | --- |
| 管理员 Admin, 经理 Manager (店长, 副店长) | Yes | Yes |
| 店员 Staff | Yes | No |
| 查看者 Viewer, the store computer's account included | Yes | No |
| Signed out, or an account without a role | No | No |

The server checks this on every request (`role_required('viewer')` to read, `role_required('manager')` to write), so a
staff or viewer write gets 403 whatever the page shows. Staff and viewers never see the edit buttons.

Notes on editing:

- A record keeps at least one photo. To replace the only photo, add the new one first, then remove the old one.
- Deleting asks first. It removes the record, its photo rows and its photo files at once. It cannot be undone.
- The page reads the list again every 5 minutes, so a store computer left open shows new records.

## Photos

- Each upload must be a still JPEG, PNG or WebP of 10 MB or less. The page uploads one photo per request.
- The server opens each photo with Pillow, turns it the right way up, shrinks it to at most 2048 px on its long side
  and saves a fresh JPEG. Nothing of the original file is kept: no EXIF, so no GPS position, camera or time.
- Files are stored under the private upload root, beside `payment_evidence/`: `popcore_app/uploads/watch_list/` by
  default, or the folder in `POPCORE_WATCH_LIST_DIR`. Without that setting the folder is the sibling of
  `POPCORE_PAYMENT_EVIDENCE_DIR`, so a server that moved its uploads keeps them together. They are never under
  `static/` or any public path.
- The only way to a photo is `GET /api/watch-list/photos/<id>` with a signed-in token. It answers
  `Cache-Control: private, no-store` and `X-Content-Type-Options: nosniff`. The page fetches each photo with the token
  and shows it from a `blob:` address, the way payment evidence is shown.

## Backups

- `backup_package.py` packages watch-list photos as their own kind (`watch`), so a restore keeps them, and refuses a
  package that lacks one. Databases from before this feature still back up and restore.
- `scripts/create_backup_package.py` takes `--watch-list-dir` (default: beside the payment evidence folder);
  `backup.sh` passes `POPCORE_WATCH_LIST_DIR` or that default.
- On the production server the folder is inside `popcore_app/uploads/`, which the nightly backup already archives.
  Litestream copies the database rows only, as with every other upload.

## Privacy and retention

- The page says: "只用于在店里认人，不要在 POPCORE 以外分享。 Only for recognising people in the stores; do not share outside
  POPCORE." Write what was seen, not guesses.
- Nothing expires by itself. Records stay until a manager deletes them. Resolved records stay readable under
  已解决 until then.
- Deleting removes the live copy at once. Copies remain in backups until those age out: nightly backups are kept 30
  days on the server and 60 days on the Mac, and DigitalOcean's weekly backups keep the whole server
  (`docs/operations-resilience.md`). The database rows also stay in Litestream's 30 days of history.
- The owner has not set a retention period. If one is set, delete records older than it by hand on this page.

## Checks

`popcore_app/tests/test_watch_list.py` and `popcore_app/tests/browser/check_watch_list.py` (screenshots in
`.local/watch-list/`); the release recovery check (`scripts/check_release_recovery.py`) restores one watch-list photo.
