# Local Clover checkout rehearsal

Prepared September 14, 2026 while the Canadian Clover merchant dashboard remains
blocked. This is a disposable local test tool, not an installed Clover app or a
production connector. The existing deployed `clover_sandbox.py` probe is unchanged.

## Run on this Mac

From the POPCORE repository root:

```sh
.local/frontend-venv/bin/python scripts/clover_rehearsal.py --port 5056
```

Open <http://127.0.0.1:5056/> for the simulated register, and
<http://127.0.0.1:5056/?view=phone> in another tab for the evidence view.
The phone view polls the local rehearsal every two seconds while visible.
This address is on the Mac only: an actual phone cannot reach it. Do not bind the
tool to a public/LAN interface or deploy it. Employee choices are simulated test
identities, not authentication. Use synthetic photos only.

The command creates a fresh database and upload directory under `.local/tmp` using
the existing `ReceivingFixture`. It invokes real POPCORE sale/payment APIs and
inventory transactions, reuses image validation/private storage, and blocks outgoing
provider requests. It does not import `popcore_app/app.py`, load an environment file,
or open the inventory database used by the application.

Stop with Ctrl+C; normal shutdown removes the disposable data. A forced process kill
can leave a `.local/tmp` directory. There is no resume-existing-data option. The
recovery test reopens the adapter against the same disposable database; it does not
prove deployment/process-crash recovery.

## Try the workflow

1. Create the default order: three mapped demo pieces, final subtotal 3540 cents,
   tax 460 cents, total CAD $40.00. Initial test stock is 12 pieces.
2. Select e-transfer. The phone view gets the same order and attempt identities
   without retyping products or amounts. Stock stays at 12 while unpaid.
3. Save a synthetic photo before payment, or complete the payment first and upload
   afterward. The latter remains marked **Evidence missing** until upload succeeds.
4. Simulate successful payment. The real disposable ledger creates one sale,
   one payment, and one stock deduction; stock becomes 9.
5. Replay payment completion and completed orders. Stock stays at 9.
6. In a new order, select a tender, then replace or cancel it. The original attempt
   remains cancelled and its photos never transfer to a new attempt. Completed
   payments cannot be cancelled through this action.
7. Test a split: complete 1000 cents cash, then 3000 cents e-transfer. Only the
   electronic component needs evidence. The rehearsal imports the sale once all
   completed components equal the order total; partial payment remains visible in
   the simulated checkout until then.
8. Switch the simulated phone employee to an unassigned Downtown employee or
   a Markham employee. No Downtown payment task is available to either one.
9. Choose **Unmapped item** or sell more pieces than available. The paid fact is
   retained with pending inventory allocation; no stock is silently fabricated.

The original total and suggested payable are retained separately from the supplied
final subtotal/tax/total. Editing these fields demonstrates preservation of facts,
not an approved discount or tax algorithm. The synthetic order's business date is
captured once and preserved when delivery is retried after midnight. A future real
adapter must use the source merchant's business-date semantics.

The fixture deliberately maps one synthetic item to one ordinary POPCORE piece.
It does not infer Clover `unitQty`, pack conversions, blind-box designs, fresh/opened
sets, or actual merchant item IDs. Those require representative Clover data and
explicit mappings before the real connector is implemented.

## Verification on September 14

```sh
# 14 new rehearsal checks + 10 existing sandbox-probe checks
.local/frontend-venv/bin/python -m unittest discover -s scripts -p 'test_clover*.py' -v
# 42 existing payment/evidence checks, including inherited sale/payment tests
.local/frontend-venv/bin/python -m unittest discover -s popcore_app/tests -p test_payment_evidence.py -v
```

All of the above passed. New checks cover exact equal-value order identity, duplicate
completion, split tenders, cancelled/replaced attempts, photo upload before/after
payment, invalid images, assigned-employee/store scope, paid shortages/unmapped
items, interruption between sale posting and payment import, and retry after midnight.
Refunds and physical returns are exercised through the real API in the checks;
the rehearsal UI does not offer refund/return buttons. A 1000-cent refund leaves
stock at 9; separately receiving one returned piece makes it 10. Evidence never
changes a payment's verification state.

Browser checks exercised creation, tender selection, exact phone handoff, synthetic
image upload before and after payment, cancellation, employee selection, and duplicate
completion with stock staying at 9. Layouts were inspected at 390 × 844 and
1200 × 900. A multi-tab stale-page scenario was not run; stale cancelled-attempt
requests are covered by the API tests. These are local browser checks with synthetic
identities; they do not verify Auth0, device camera capture or Clover delivery timing.

Independent review found a business-date drift on next-day retry; a failing
regression reproduced it, the date is now persisted with the order, and the reviewer
verified that fix. Production source, static assets, dependency lockfiles, live
credentials and the deployed sandbox probe were not modified. No commit/deployment.

## What remains

- Complete real sandbox app installation, OAuth/token rotation and authenticated
  webhook delivery after the Clover dashboard issue is resolved.
- Inspect actual Canadian order, item, payment, cashier and tender fields. Establish
  the durable source contract, store/product/employee mappings and catch-up rules.
- Implement and test the Clover Android tender/modify-order bridge. This rehearsal
  deliberately does not claim to emulate Clover Register or Station Duo.
- Confirm the amount suggestion formula, tax treatment and Clover's interpretation
  of the proposed payment-dependent discounts/surcharges.
- Integrate the phone workflow with real Auth0 identities and explicit employee
  assignment. This test's simulated actor selector must never be deployed.
- Prove real service restart/refresh-token recovery, attachment backup/restore,
  delayed/offline sales and historical cutover against reviewed opening inventory.
- RewardUp, WooCommerce coordination and other store integrations remain separate
  deliverables; this build is the first independent checkout rehearsal.
