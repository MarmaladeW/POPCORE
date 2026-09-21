# Staff home

Implemented locally on `codex/staff-home`, based on `codex/catalog-sales-rework` at `32b7a9cf`, with the existing uncommitted checkout implementation brought across from `popcore-checkout`. The source workspaces are unchanged. This branch has not been deployed or built into the committed production bundle.

The staff entrance `/` offers 买单 (`/checkout`), 入店 (`/incoming`), 娃娃机 (`/claw`), and 汇总 (`/summary`). Viewers retain the read-only Today screen. Home, Checkout and Schedule stay in mobile navigation. The existing Today task list remains available at `/today`, including older unfinished goods work.

Special orders (`/special-orders`) is a separate Daily work page for customer-requested items. Staff record a free-text request, customer phone, total price, and each payment as a separate entry. Full payment is required before recording that the customer received the item. Managers and admins can always view the phone number; staff can view it only while assigned the `Cashier` position on that Toronto calendar day. Admins can correct creator/completer attribution and dates. These records do not post inventory, checkout, sale, closing, or Clover activity and are not tied to a store.

The existing attendance component shows the employee's assigned shift and punch-in before the four choices. It disappears only after the server confirms punch-in for the Toronto business date. Schedule retains attendance and shift access. Home keeps recent events and personal shifts below the choices.

入店 links the existing receipt and transfer workflows, lists today's authorized incoming documents, and provides display-arrival recording. Existing opening-count, inventory access, version, transit, and manager-draft safeguards remain in force.

娃娃机 records prizes, replenishments and card/e-transfer exchanged for equal cash. Product choices use exact catalog IDs; actor, date and timestamp come from the server. Cash exchange requires a payment reference/note and explicit UI confirmation that payment was received and the same cash handed over. The backend writes its event and one cash payout in a transaction, reducing the expected drawer through the existing closing calculation. It creates no merchandise sale. Unknown write outcomes retain the request key and original payload; retry does not post again.

Claw prizes, claw replenishments and display arrivals are operational records only: **they do not change stock balances**. The UI states this. Their physical inventory identities/mappings still require review before ledger posting can be enabled. No opening balances or existing legacy stock are inferred from these records.

汇总 presents recorded completed checkouts, operational events and authorized incoming documents with copyable text. Order items and actual payment methods remain explicit; split orders appear once. The familiar headings distinguish claw prizes, replenishment, exchanges and display arrivals. Historical imports remain available to managers via Sales; they are not combined again with newly generated checkout totals. Staff see only their own checkout/event history, plus goods allowed by inventory access. Managers use today's assigned locations; admins retain all-store read scope. The page refreshes authorization across Toronto midnight even when a historical date is selected.

Clover remains disconnected. This implementation does not verify live order synchronization, provider tax/discount behavior, Auth0 login or physical phone cameras. The summary is not evidence that all external sales were imported; closing still requires source-completeness checks.

## Verification

Run backend tests with `python -m unittest discover -s popcore_app/tests -v`. Run `npm test` and `npm run build -- --outDir ../../.local/frontend-build` from the frontend directory; the latter preserves committed release assets. Browser checks: `check_staff_home.py`, `check_checkout_focus.py`, `check_attendance.py`, `check_special_orders.py`, and `check_foundation.py` under `popcore_app/tests/browser`. Home and checkout checks include actual Flask/SQLite flows with disposable data and test authentication. Never deploy test authentication.
