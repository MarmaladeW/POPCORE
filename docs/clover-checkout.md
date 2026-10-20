# Clover checkout

Status (2026-10-20): built on branch `codex/clover-checkout`. Unit, connector and browser checks pass against a fake Clover, and two live sandbox orders on the Clover emulator completed on both sides (see Verification). The owner's hands-on run is still to do. Production Clover, the production database and the live POPCORE site are untouched.

POPCORE is the operating centre and the record of every sale, payment and stock movement. Clover scans items and takes card. Card sales end paid on Clover. A sale paid in POPCORE (cash, e-transfer, WeChat Pay, Alipay, or the rest of a split) leaves the Clover order open, titled with the date and time it was paid, for example `Paid 2026-10-20 11:23` (owner, 2026-10-20).

This replaces the sandbox rehearsal in [clover-checkout-rehearsal.md](clover-checkout-rehearsal.md), whose separate `/checkout?source=clover-sandbox` screen and throwaway database are gone.

## How it fits together

- Each Clover order is one ordinary POPCORE checkout. Its reference is the 13-character Clover order ID, exactly as the Clover Orders app shows it, and its business date is the Toronto date Clover created it. The side table `clover_orders` keeps Clover's snapshot, state and write-back progress.
- A new order arrives unclaimed under **Clover register**. The cashier picks it up by tapping **Received**, by **Pick up this order**, or automatically (below). **Release order** hands it back while no money is recorded in POPCORE.
- Lines keep Clover's original price. A line is matched to a POPCORE product by Clover item ID (set by `scripts/import_clover_items.py`), otherwise by an unambiguous one-unit barcode. Unmatched lines stay as Clover's text. As with any already-paid sale, the sale then waits for stock allocation, which blocks closing until a manager allocates it.
- Card payments taken on Clover become completed payments in POPCORE. Clover-confirmed card payments are recorded as verified. Device snapshots update the items of an unpaid order but never touch payments.
- A sale paid entirely on Clover is recorded by the Clover register, even when no POPCORE screen is open. A 5-second background job does this and retries Clover updates.

## At the counter

| Customer pays | What the cashier does |
|---|---|
| Card | Pay → Card on Clover. POPCORE records the sale when Clover confirms it. |
| Cash, e-transfer, WeChat Pay or Alipay | Tap the tender. Its button already shows the price, including any automatic discount. Collect, then tap **Received**. E-transfer, WeChat Pay and Alipay ask for a payment photo next; WeChat Pay and Alipay show the CNY amount to charge, and the payment keeps that amount and its rate. POPCORE titles the Clover order `Paid <date> <time>` and leaves it open; on the device tap **Review Order → Save** to clear the screen. |
| Part card, part another tender | **Split payment**, type the card part. POPCORE shows the device steps (**Pay → ⋮ → Split Custom Amount → $X → Card**) and the rest to collect. Either part can come first. Once the card lands, the rest is recalculated from the actual card amount. Tap **Received** for the rest; POPCORE titles the Clover order `Paid <date> <time>`, which stays partly paid there; on the device tap **Pay more later**. |
| Two non-card tenders | **Split payment → Split without card**, record each part with **Received**. POPCORE titles the Clover order once the order is fully paid. |

- **Open new Clover orders automatically on this device** (on by default, remembered per device): an idle, visible checkout screen opens and picks up an order scanned after the screen loaded. An open order is never switched; a finished order still waiting for its payment photo counts as busy. The oldest new order wins; another screen that picked it up first is skipped.
- **Find** takes a Clover order ID and opens that POPCORE order.
- **Take back** (the cashier who recorded it, or a manager; reason required) removes a POPCORE payment from an order that is not yet a sale. Use it when change was given back or an amount was mistyped. It is also how an overpaid split is fixed.
- **Cancel this order** deletes the unpaid order on Clover as well. Clover refuses when it already has a payment. If Clover cannot be reached, a manager can **Cancel in POPCORE only**; POPCORE deletes it on Clover later. An order deleted on Clover is cancelled in POPCORE when no money was recorded; otherwise it is flagged.

## Prices

The server works out every amount from Clover's current total A (`popcore_app/clover_pricing.py`); the screen only shows them.

| Clover order | Card only | Other tenders only | Split with card |
|---|---|---|---|
| No other discount | A | Cash discount: up to $15, the nearest whole dollar to 2.5% off, below A; above $15, at least $1 off (or 2.5% when more), rounded down (same rule as manual checkout) | Rounding: A down to a whole dollar |
| RewardUp or any other discount already on it | A | Rounding: A down to a whole dollar | Rounding: A down to a whole dollar |
| An old manual cash price (`CA$x.xx Off`) | Not offered | A | A |

- Totals under $10 are not rounded for splits. If the card part alone reaches the rounded amount, the split falls back to A.
- Orders whose total POPCORE cannot reproduce exactly (weighed or modified lines, several percentage discounts, tax groups that disagree) get no automatic discount: every tender pays A.
- Sale money: subtotal is Clover's item prices; tax is A minus the discounted subtotal; collected is the money actually received; the reduction is the rest.
- The cashier's screen sends the amount it showed. If Clover changed the order meanwhile, POPCORE refuses (`price_changed`) and shows the new amount.

## What POPCORE writes on Clover

Only after the sale is recorded, and only when money was recorded in POPCORE; a sale paid entirely on Clover needs nothing.

`CLOVER_WRITEBACK=label` (the default, owner 2026-10-20): one idempotent request, `POST /orders/<id>/label`, sets the order's title to `Paid <date> <time>` (Toronto time of the last payment recorded in POPCORE) and adds the same line to its note. No payment or discount is added, so the order stays open on Clover, Clover's reports do not count that money, and its total stays Clover's original total. A cash-discount or rounding price exists only in POPCORE.

`CLOVER_WRITEBACK=pay` instead completes the order on Clover so it shows paid. It never writes while the order is open, because Clover drops an order from Register as soon as any payment is added. One idempotent request, `POST /orders/<id>/settle`, then does the following.

1. Checks that Clover's items, discounts and card payments are still what POPCORE recorded; otherwise nothing is written (`clover_changed`).
2. Adds one discount named **Cash discount** or **Rounding** after any existing ones, and sets the order total. Clover does not recompute totals, so the connector calculates the total the way Clover does: percentage then amount discounts, per tax rate, rounded half up. The total is never above the money received and never more than 5¢ below it; the largest POPCORE tender is reduced by that difference on Clover only, and POPCORE keeps the amount actually received.
3. Titles the order `Paid <date> <time>`; each POPCORE payment's note says the same. Clover refuses a new external reference once an order has a payment, so none is set.
4. Adds each POPCORE tender as an external payment keyed `popcore-<instance>-<checkout>-<payment>`, with its share of tax, so a retry never pays twice.
5. Reads the order back and requires `PAID`.

A failed write never undoes the POPCORE sale. It shows **Not labelled on Clover yet** (or **Not on Clover yet** in pay mode) with **Retry** and is retried after 5 s, 15 s, 1 min, then every 5 min, honouring Clover's `Retry-After`. A manager can mark a failed order **Fixed on the Clover device instead**, with a reason. The connector's write routes need the writer key; without it POPCORE only reads Clover.

## Review and closing

| Situation | Effect |
|---|---|
| A Clover order of the day still open | Hard blocker (existing `checkout_unresolved`) |
| Paid in POPCORE, Clover order not labelled (or, in pay mode, not paid) yet | Hard blocker `clover_writeback_pending` |
| The day's Clover orders not scanned in the last 10 minutes | Hard blocker `clover_sync_stale` (closing scans the day itself first) |
| Clover changed, refunded, voided, credited or deleted the order after the sale, or took another payment | Exception `clover_review`, needs a reason |
| More money received than owed; an unknown Clover tender; deleted on Clover after money was recorded | The open order stops and shows the reason |

Staff on shift can read and close a day that holds the Clover register's sales of that day.

## Configuration

POPCORE's private environment (`popcore_app/.env` locally):

| Setting | Meaning |
|---|---|
| `CLOVER_CONNECTOR_URL` | The connector, HTTPS (or HTTP only to loopback), e.g. `https://popcore.store/clover-sandbox` |
| `CLOVER_CONNECTOR_PASSWORD` | The connector's `ADMIN_PASSWORD` (user `popcore`) |
| `CLOVER_CONNECTOR_WRITER_KEY` | At least 32 characters, equal to the connector's `POPCORE_WRITER_KEY`. Leave it out for a read-only copy |
| `CLOVER_MERCHANT_ID`, `CLOVER_STORE_ID` | The 13-character merchant and the POPCORE store that takes its orders |
| `CLOVER_ENVIRONMENT` | `sandbox` (default) or `production`; must match the connector |
| `CLOVER_INSTANCE` | Short tag in payment keys (default `pc`), so two POPCORE copies never share keys |
| `CLOVER_IMPORT_SINCE` | Toronto date or time; older Clover orders are ignored |
| `CLOVER_WRITEBACK` | `label` (default): leave orders paid in POPCORE open on Clover, titled `Paid <date> <time>`. `pay`: pay them on Clover with POPCORE's discount and tenders |
| `CLOVER_BACKGROUND_SYNC=1` | Runs the 5-second job even with `DISABLE_SCHEDULER=1` (development) |

The Clover app needs Read Merchant, Employees and Inventory, plus Read and Write Orders and Payments. Clover applies changed permissions only after the app is uninstalled and reinstalled on the merchant. The merchant needs Cash and custom tenders named E-transfer, WeChat and Alipay.

The Clover store must use authoritative inventory with a verified opening, and the Clover register gets an inventory grant for that store the first time POPCORE syncs. For local hands-on testing, `scripts/setup_clover_local.py` backs up the database, then sets up Midtown with test products for the sandbox items and opens every location. That switches the local database to authoritative inventory. It is a dry run unless given `--apply`.

## Verification

- `popcore_app/tests/test_clover_pricing.py`: prices, sale money, keys and tender names.
- `popcore_app/tests/test_clover_sandbox_probe.py`: the connector's Clover calculation, settle sequence, refusals, delete and single-order reads.
- `popcore_app/tests/test_clover_checkout.py`: import, device snapshots, cash, splits in either order, overpaid and take back, card-only in the background, write-back failure and retry, changes and refunds after the sale, cancel and delete, claim and release, closing blockers and the migration.
- `popcore_app/tests/browser/check_checkout_focus.py`: real Flask routes with a fake Clover at phone width. Covers automatic pick-up, the cash price, a split with card, cancel, find by ID and retry.

None of these proves live Clover, the Clover devices, Auth0 or production.

Live sandbox, 2026-10-20, on the Flex emulator with the hosted connector and the owner's local database. Payments were recorded through POPCORE's own receive path under the local test identity `local|claude-smoke`; its temporary Midtown grant was removed afterwards.

- `V6N6W4ZV6R1HC`: the device order reached POPCORE within seconds, unclaimed, with both lines matched. Cash was priced at $42.00 instead of $42.59. In 2.4 s Clover showed it PAID at $42.00, with a Cash discount of −$0.52, a Cash payment keyed `popcore-mac-1-1` carrying $4.83 tax, reference `POPCORE-1`, and both items at their original prices. Register cleared by itself.
- `T5TZ2XXJHQMD0`: Pay → ⋮ → Split Custom Amount → $22.00 on the device, with E-transfer standing in for card. The device showed $20.59 remaining. POPCORE asked for $20.00 and recorded it. The first write-back failed because Clover refuses a new external reference on an order that already has a payment; the connector now labels such orders through the title and payment notes. The retry left Clover PAID at $42.00: Rounding −$0.52, $22.00 E-transfer ($2.53 tax) and $20.00 Cash ($2.30 tax). The device returned to an empty Register.
- After later Clover reads, both orders stayed completed and showed no review. Stock moved once per sale.
- The owner's own run the same morning found two things. Items after the first appeared 8–20 seconds late, because the Android companion's six-hour sync session had ended at 07:44 and only Clover's own cloud sync was left; tapping **Start device sync** restored it. And the owner chose to leave orders paid outside Clover open there, labelled with the date and time, which is now the default.

## Not covered

The production Clover app and merchant, deploying POPCORE, distributing the Android companion (needs Clover's APK approval), and real opening counts. Downtown and Markham cannot take Clover orders until their openings are verified (the existing inventory rule).
