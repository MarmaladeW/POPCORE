# Clover local rehearsal

User direction: do the independent work while the Clover merchant dashboard is blocked.
This implements the isolated checkout, phone evidence, and ledger rehearsal described
in this task. It does not enable a production connector.

## Design

Reuse `ReceivingFixture` to create a disposable POPCORE database and the real sales,
payment, inventory and evidence validation services. A separate loopback-only Flask
test interface simulates a register and phone. Synthetic identities are explicit;
the application authentication code and public service are unchanged. All outgoing
provider requests are blocked by the existing test fixture.

Persist simulated orders, attempts and staged evidence in that disposable database.
Orders retain original, suggested and final amounts. Final subtotal and tax are
entered explicitly: no discount or tax algorithm is invented. Exact simulated
merchant/order/attempt IDs link payments, never amounts or timestamps.

A replacement tender cancels the pending attempt; completed partial payments remain.
Only an order whose completed tenders sum to its final total is imported. The import
reuses source uniqueness and the existing sales posting/payment services, then links
staged photos to the exact payment. A retry inspects durable source records, resumes
unfinished stages and never posts a second sale. Unknown products retain a paid sale
with pending inventory allocation.

Photo uploads reuse image validation, metadata stripping and private publication.
Only the simulated cashier, designated photo employee, or scoped manager can use
the task. Uploader and cashier remain separate. Cancelled attempt photos never move
to a replacement attempt. Uploading evidence never marks money verified.

This is a local rehearsal, not an externally accessible phone service. Recreating
the rehearsal adapter against the same database tests interruption recovery; exiting
the CLI removes disposable data. Real service crash/restart and production recovery
remain separate checks.

## Execution and checks

- [x] Add `scripts/test_clover_rehearsal.py` with end-to-end assertions for
  12 → 9 stock, split tenders, exact equal-value identities, cancellation, evidence
  timing, invalid images, access scope, recovery and unknown item allocation.
- [x] Implement `scripts/clover_rehearsal.py` using the existing fixture/services;
  add no dependencies, production routes, live writes or environment overrides.
- [x] Add `scripts/clover_rehearsal.html`: register inputs, phone evidence queue,
  current stock, explicit simulation status, visible errors, native upload inputs.
- [x] Run the new checks, existing sandbox checks and existing payment/evidence
  checks with `.local/frontend-venv/bin/python -m unittest discover`.
- [x] Exercise the rendered local demo at desktop and phone widths, including
  upload, cancellation and duplicate replay. Stale-attempt rejection is covered
  by the API checks; a multi-tab stale-page browser scenario was not run.
- [x] Document commands, passing evidence and remaining provider/device gates in
  `docs/clover-rehearsal.md`. Review only new files; do not commit or deploy.

## Subsequent provider-dependent work

Complete sandbox OAuth and webhook delivery; inspect real item/payment fields;
build the Clover Android tender/modify-order bridge against verified contracts;
confirm the amount suggestion/tax rule and vendor policy; test on appropriate
Station Duo hardware. Real merchant mappings, Auth0 cashier/phone assignment,
store opening/cutover, RewardUp and WooCommerce remain explicitly separate.
