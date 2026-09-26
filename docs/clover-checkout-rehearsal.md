# Clover checkout rehearsal (option 1)

Status: implementation and offline checks exist. The user-operated Android-to-phone timing rehearsal is still pending; confirm the current deployed app and probe versions before starting it.

## Boundaries

`/checkout?source=clover-sandbox` uses the same checkout components as normal checkout. Its requests go exclusively to `/api/clover-sandbox/checkouts`. It cannot finalize a real sale, post stock, record a manual payment, cancel or refund Clover transactions. Payment completion comes from Clover's `PAID` state plus matching successful payment amounts. Partial or failed payments do not complete checkout. Zero-total orders and payment-total discrepancies remain open for review on Clover.

Order snapshots, stable payment IDs, cashier ownership and image bytes live in one separate SQLite database. The real POPCORE database is only read for identity, roles, stores, shifts and existing inventory-access permissions. Its sales, inventory and checkout tables are not written. The adapter requires the existing sandbox probe on loopback and refuses non-sandbox responses; OAuth tokens remain with the probe.

## Enable after deployment is authorized

1. Release the application source and an intentional frontend build through the normal POPCORE release process. Do not enable the adapter with an old frontend build.
2. Update the isolated probe with `scripts/clover_sandbox.py` from this branch. This preserves its OAuth/webhook/read-only behavior, uses expanded order payments, and resolves at most one missing tender label per read to avoid payment-detail rate limits. Keep the existing private config and data untouched. Keep one Gunicorn worker for refresh-token rotation and bind only to `127.0.0.1:5055`.
3. Confirm Midtown's exact active `stores.id` using a read-only lookup. Do not infer it from a numeric position or invent an ID. Set `CLOVER_SANDBOX_STORE_ID` to that ID in the application's private service environment.
4. Create a dedicated directory owned by the POPCORE application service account, mode `0700`, outside the served/static/upload trees, for example `/var/lib/popcore-clover-checkout`. Set `CLOVER_SANDBOX_CHECKOUT_DIR` to its absolute path. The adapter creates only `clover-checkout-sandbox.sqlite3` plus SQLite journal sidecars there, with the database mode `0600`. It refuses to reuse that database for a different store.
5. Set `CLOVER_SANDBOX_PROBE_PASSWORD` privately to the existing probe's `ADMIN_PASSWORD`. Never put it in `VITE_*`, git, a URL or client-side code. No production Clover credentials are needed. The source URL is fixed to the loopback sandbox probe, with redirects disabled.
6. Restart through the approved release procedure. Checkout displays an **Open Clover sandbox rehearsal** link when configuration is enabled. Staff still need an active employee, a shift at Midtown today and existing inventory access to claim orders or upload evidence. Admin permissions retain their existing meaning. No bypass is added.

## User-operated rehearsal (not run yet)

On the phone, open Checkout → Open Clover sandbox rehearsal. Midtown is the only available location. The amber banner distinguishes this from real operations.

Create an order on Android. It should join the queue; tap it to claim it. New arrivals do not switch the selected order. Choose Cash, Card or E-transfer to see the existing pricing guidance. Enter discounts and take payment on Android. Once a discount or payment is present, use Clover's recorded total rather than suggesting another discount. Split amounts are entered in Clover, not recorded twice in POPCORE.

E-transfer has already been added by the user. This adapter recognizes its label; it does not create tenders. WeChat Pay and Alipay must be actual matching custom tenders before use. Check is never remapped to either. Unknown labels remain visible as reported by Clover.

Successful electronic payments expose the existing camera/upload control. Evidence remains attached to that specific sandbox payment, including in history. Repeating an upload of identical normalized image content is idempotent. Staff history is limited to their claimed orders; another cashier cannot take over a claimed order by tapping it.

The page targets a one-second poll cadence, without overlapping requests, and shares a half-second server cache across app workers. A slow request finishes before the next starts. Polling pauses when hidden or when a mutation is unresolved. Reads have a finite timeout. A stale/failed source retains the previous snapshot with a warning. Five seconds without a fresh per-order snapshot disables checkout guidance/claiming independently of whether the next network request returns; evidence for an already recorded payment remains attachable under the usual permissions.

The target is **under three seconds end to end**, not a verified latency guarantee. Measure from the Android action to the phone update. Network time, Clover cloud delivery, OAuth refresh and provider rate limits count toward the result.

The probe returns only its latest 20 modified orders. An open draft with no total or payments is not queueable and is skipped, even if it already has items; it enters the queue when Clover provides a total. Other invalid orders still fail validation. Observed orders remain in isolated history, but orders outside that window are not continuously reconciled. Missing orders are never interpreted as cancelled. Refunds, voids, deleted orders, weighted/complex-item pricing and historical imports require separate provider coverage; use Clover as the source of truth. This is not production ingestion and must not be enabled for a real merchant.

## Navigation

Daily work now has Home, Checkout, Receive goods, Claw machine and Summary. Inventory expands to Stock & movements and Restock. More expands to Special orders, Store tasks and Trades. Manager review and planning/admin permissions remain unchanged. Phone More groups the same destinations. Labels are short; bilingual headings remain inside the workflows.

Order history is inside Checkout; manual sale entry is an explicit fallback there. Closing is inside Summary. Existing URLs remain valid and select the appropriate parent navigation item.

## Cutover and cleanup (do not execute during implementation)

1. Disable the adapter by removing its three environment values and restart the application; stop the isolated sandbox probe. Confirm no process is writing either sandbox database before cleanup.
2. Resolve and inspect the exact private checkout directory and the probe's configured `DATA_DIR`. List their contents and confirm they contain only sandbox data. Do not target the workspace root, main POPCORE database, production uploads or real historical sales.
3. With explicit cutover authorization, remove the exact sandbox checkout SQLite file and its `-wal`/`-shm` sidecars. This removes all observed sandbox orders, claims and payment photos together. Separately remove the probe's sandbox SQLite file and sidecars (tokens, OAuth states and webhook events) and any specifically identified sandbox-only logs/artifacts. Do not delete unrelated data or credentials.
4. Remove the sandbox-only service/config and nginx exposure as part of the approved cutover. Revoke the sandbox connection if it will no longer be used. Clover-hosted sandbox records are separate from local files; remove/reset those through the sandbox account only, never a production merchant.
5. Check scoped backups too: deleting current files does not erase retained backup copies. Keep sandbox checkout/probe data out of production backups; remove any identified sandbox-only copies according to the approved cleanup scope.

No cleanup, credential change, production app creation or data deletion is performed by this implementation.

## Deferred developer check

`python -m unittest discover -s popcore_app/tests -p test_clover_sandbox_adapter.py -v` is a small offline check of failed/partial/successful/mismatched payments, stale permissions, tender labels, unpriced drafts and separate storage. It is not a replacement for the existing checkout regression suite, frontend build, permissions/evidence checks or the user-operated Android-to-phone rehearsal.
