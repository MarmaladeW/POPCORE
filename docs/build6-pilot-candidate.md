# Build 6 pilot candidate and deployment sequence

The local candidate is review evidence, not approval to cut over a store. It combines the current uncommitted Builds 1–6 source bytes with an isolated production frontend build and a hash manifest. The manifest records the GitHub remote, branch, baseline HEAD, full dirty/untracked state, runtime versions, commands, files and live gates.

## Deployment and rollback sequence

1. Confirm an approved candidate manifest, the prior working release, the complete backup destination and rollback owner. Verify real Auth0 callbacks/origins and CSP reports.
2. Announce a maintenance boundary and stop all store writes. Create a complete database-plus-attachments package and copy it to the approved off-host destination. Verify hashes and an isolated restore.
3. Confirm store access, physical opening status, unresolved catalog identities, coin routine, trade/condition policy and device checks. Any unresolved item blocks its affected workflow.
4. Install the allowlisted candidate files, preserve `.env` separately, set `/var/lib/popcore`, `/var/log/popcore` and `/var/cache/popcore` ownership, then run additive migrations once through the service start.
5. Validate service status, nginx configuration, TLS/security headers on success and error responses, private evidence denial, Auth0 sign-in, DT/MK scope, Today shifts, one sale/payment/evidence flow, reports and closing reconciliation.
6. Reopen writes only after the smoke facts reconcile to database/report facts. Record candidate and backup hashes with the pilot result.
7. Trigger rollback for startup failure, authorization leakage, unreconciled stock/money, unavailable private evidence or failed closing. Stop writes and preserve the post-attempt database first. If the new schema received writes, prefer reviewed forward recovery or reconciliation; do not restore an old database and discard them. Restore application files only when schema compatibility is confirmed, otherwise restore the complete reviewed package to a new path and re-verify before switching paths.

## Live gates

- Approved operational inventory access and physical opening counts per location
- Opening/retained-coin routine and exception approval thresholds
- Trade proof and condition handling policy
- Actual scanner and camera behavior on shop devices
- Host ownership, private storage, backup credentials, off-host destination and retention
- Real Auth0 callbacks, origins, CSP reports and TLS headers
- Prior working release and complete backup location

Schedule audit findings, Clover/WooCommerce synchronization and purchasing remain deferred. Local synthetic evidence does not approve a live cutover.

## Local evidence limits

The real-service browser pilot proves sale entry, stock deduction, private payment evidence, manager payment/evidence review, tender reporting and closing startup through React, Flask and SQLite. The longer receiving, transfer, restock, count, trade, refund/return, late-adjustment and final-close sequence is covered by real backend integration tests plus focused browser fixtures, not by one end-to-end real-service browser session. Extending that browser pilot is the remaining local evidence action before treating the complete store-day flow as browser-proven.

Linux syntax validation is also outstanding because this Windows host has no Bash, nginx, systemd validator or accessible WSL runtime. Run the exact commands in `docs/production-readiness.md` on the isolated Linux candidate host or CI. Physical device behavior, store policy, Auth0/CSP/TLS and off-host recovery remain the named live gates above.
