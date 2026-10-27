# Clover checkout

Status (2026-10-20): built on branch `codex/clover-checkout`. Unit, connector and browser checks pass against a fake Clover, and two live sandbox orders on the Clover emulator completed on both sides (see Verification). The owner's hands-on run is still to do. Production Clover, the production database and the live POPCORE site are untouched.

POPCORE is the operating centre and the record of every sale, payment and stock movement. Clover scans items and takes card. Card sales end paid on Clover. A sale paid in POPCORE (cash, e-transfer, WeChat Pay, Alipay, or the rest of a split) leaves the Clover order open, titled with the date and time it was paid, for example `Paid 2026-10-20 11:23` (owner, 2026-10-20).

This replaces the sandbox rehearsal in [clover-checkout-rehearsal.md](clover-checkout-rehearsal.md), whose separate `/checkout?source=clover-sandbox` screen and throwaway database are gone.

## How it fits together

- Each Clover order is one ordinary POPCORE checkout. Its reference is the 13-character Clover order ID, exactly as the Clover Orders app shows it, and its business date is the Toronto date Clover created it. The side table `clover_orders` keeps Clover's snapshot, state and write-back progress.
- A new order arrives unclaimed under **Clover register**. The cashier picks it up by tapping **Received**, by **Pick up this order**, or automatically (below). **Release order** hands it back while no money is recorded in POPCORE.
- Lines keep Clover's original price. A line is matched to a POPCORE product by Clover item ID (set by `scripts/import_clover_items.py`), otherwise by an unambiguous one-unit barcode. Unmatched lines stay as Clover's text. As with any already-paid sale, the sale then waits for stock allocation, which blocks closing until a manager allocates it.
- A paid order with no items (a custom amount or quick sale on Clover) is imported too, as one line `Clover payment with no items`, so its payment counts at closing (owner, 2026-10-27). Its sale waits for stock allocation; on the sale page a manager matches it to products or ticks **Charge without stock**, which records it with the reason and moves no stock. An empty order with a $0 total is not imported.
- Only orders created after the store went live are imported (owner, 2026-10-27): the store's opening count already holds what was sold before it, and while a store has no opening its orders are skipped and the checkout screen says so. Connect Clover to a store only after that store goes live.
- Blind boxes over half a set are answered before payment, as on a manual order ([POPCORE checkout](popcore-checkout.md)): the answer stays with the product when Clover's items are read again. A sale paid entirely by card on Clover before anyone answered waits for stock allocation, and a manager answers on its sale page.
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

## Sales started in POPCORE, card on Clover

Some products exist only in POPCORE: confirmed designs such as `PM0209-01` 小王子 The Businessman have no Clover item, and their prices move with demand. For these the cashier builds the sale in POPCORE and Clover only takes the card.

1. **Manual checkout**: choose the exact designs and any other items, agree each line's price and the tax, **Create pending checkout**.
2. On the order, tap **Send to Clover for card**. POPCORE opens an open Clover order with one custom line per unit, named by product ID and name (`PM0209-01 小王子 The Businessman`), at the agreed price, with the merchant's 13% HST rate. An agreed discount goes on as one order discount, **Agreed discount**. The order's title (**Order #** on the device) is the POPCORE reference, `MT-20261026-001`; its external reference (**Invoice Number**) is the same reference shortened to Clover's 12 characters, `MT261026001`.
3. On the device: **Orders** → tap the order → **Add Payment** → **Card**. POPCORE records the sale on the same checkout when Clover confirms it, as for any order paid entirely on Clover: card payment verified, nothing written back, stock moved on the exact designs.

- The Clover order joins the POPCORE checkout it came from. It never arrives under **Clover register** as a new order: the send links it straight away, and if that reply is lost, the order is matched by its external reference and lines when Clover's feed shows it.
- **Sending is refused, with nothing created on Clover,** when Clover would charge another total. Clover adds 13% HST after any discount, so a hand-typed tax, or 13% taken before a discount, gives a different total. The message shows Clover's amount; fix the tax or prices, or take payment in POPCORE.
- While Clover has not confirmed the order (connection down), the order shows **Not on Clover yet** with **Retry** and **Stop sending**, and takes no payment in POPCORE. Retrying never makes a second Clover order. Stop sending makes it a plain POPCORE order again.
- Once on Clover, the order is a Clover order in POPCORE: cash, e-transfer, WeChat Pay, Alipay and splits work exactly as for scanned orders below (cash discount, rounding, `Paid <date> <time>` label, order left open on Clover). **Cancel this order** deletes it on Clover. It cannot be released to the Clover register.
- Only an open order with no money recorded can be sent. A $0 line cannot go to Clover; at most 100 units per order.

## Prices

The server works out every amount from Clover's current total A (`popcore_app/clover_pricing.py`); the screen only shows them.

| Clover order | Card only | Other tenders only | Split with card |
|---|---|---|---|
| No other discount | A | Cash discount: under $10, the previous whole dollar ($5.10 to $5; a whole-dollar total keeps its price); $10 to $15, the nearest whole dollar to 2.5% off, below A; above $15, at least $1 off (or 2.5% when more), rounded down (same rule as manual checkout) | Rounding: A down to a whole dollar |
| RewardUp or any other discount already on it | A | Rounding: A down to a whole dollar | Rounding: A down to a whole dollar |
| An old manual cash price (`CA$x.xx Off`) | Not offered | A | A |

- Totals under $10 are not rounded for splits. If the card part alone reaches the rounded amount, the split falls back to A.
- Orders whose total POPCORE cannot reproduce exactly (weighed or modified lines, several percentage discounts, tax groups that disagree) get no automatic discount: every tender pays A.
- Cash taken in POPCORE is rounded to 5¢, because Canada has no pennies (owner, 2026-10-27); E-transfer, WeChat Pay and Alipay stay exact. When cash pays what is left, ending in 1–2 goes down to 0, 3–4 up to 5, 6–7 down to 5 and 8–9 up to 10: $20.37 by card on Clover leaves $21.63 of a $42.00 split, collected as $21.65, and the order is paid at $42.02. Cash is received in 5¢ steps. The difference is part of the sale's reduction; only above an order with no discount are the 1–2¢ its rounding.
- Sale money: subtotal is Clover's item prices; tax is A minus the discounted subtotal; collected is the money actually received; the reduction is the rest.
- The cashier's screen sends the amount it showed. If Clover changed the order meanwhile, POPCORE refuses (`price_changed`) and shows the new amount.

## What POPCORE writes on Clover

**Send to Clover for card**: one idempotent request, `POST /orders/create`, with the checkout's key `popcore-<instance>-<checkout>`, its reference, the lines, any agreed discount, the tax rate POPCORE priced with and the total it expects. The connector:

1. picks the merchant's tax rate at that percentage (the default one if several), and refuses (`tax_missing`, listing the rates) when there is none or it is ambiguous;
2. works out Clover's total with the same calculation as `settle`, and refuses (`amounts_differ`) before writing anything when it is not the expected total;
3. reuses the order it already made for that key, or an order of the last two days carrying the reference that no other key sent; otherwise creates an `open` order with the title and external reference;
4. adds the lines in one bulk request (each with the tax rate's id, name and rate), then the discount and the total, skipping each step Clover already shows; an order already being paid on the device is never rewritten, and one whose lines differ is refused (`clover_changed`);
5. reads the order back, requires a tax rate on every line (`tax_not_applied`), and returns it with Clover's pricing check.

Otherwise, only after the sale is recorded, and only when money was recorded in POPCORE; a sale paid entirely on Clover needs nothing.

`CLOVER_WRITEBACK=label` (the default, owner 2026-10-20): one idempotent request, `POST /orders/<id>/label`, sets the order's title to `Paid <date> <time>` (Toronto time of the last payment recorded in POPCORE) and adds the same line to its note. No payment or discount is added, so the order stays open on Clover, Clover's reports do not count that money, and its total stays Clover's original total. A cash-discount or rounding price exists only in POPCORE.

`CLOVER_WRITEBACK=pay` instead completes the order on Clover so it shows paid. It never writes while the order is open, because Clover drops an order from Register as soon as any payment is added. One idempotent request, `POST /orders/<id>/settle`, then does the following.

1. Checks that Clover's items, discounts and card payments are still what POPCORE recorded; otherwise nothing is written (`clover_changed`).
2. Adds one discount named **Cash discount** or **Rounding** after any existing ones, and sets the order total. Clover does not recompute totals, so the connector calculates the total the way Clover does: percentage then amount discounts, per tax rate, rounded half up. The total is never above the money received and never more than 5¢ below it; the largest POPCORE tender is reduced by that difference on Clover only, and POPCORE keeps the amount actually received. Cash rounded to 5¢ above Clover's total (an order priced exactly, such as $19.13 collected as $19.15) is sent as Clover's total, with the largest cash payment 1–2¢ smaller there.
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
| A Clover order of the day that could not be imported (for example, the register's inventory grant was removed) | Hard blocker `clover_import_failed:<Clover order ID>` until the order imports on a later scan, or a manager resolves it with a reason under **Clover orders not in POPCORE** on the checkout screen; a resolved order is never imported afterwards, so its money is never counted twice (owner, 2026-10-27) |
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

The Clover store must use authoritative inventory with a verified opening, and the Clover register gets an inventory grant for that store the first time POPCORE syncs. Connect Clover to a store only after it goes live: orders created before its opening count are never imported. For local hands-on testing, `scripts/setup_clover_local.py` backs up the database, then sets up Midtown with test products for the sandbox items and opens every location. That switches the local database to authoritative inventory. It is a dry run unless given `--apply`.

## Verification

- `popcore_app/tests/test_clover_pricing.py`: prices, sale money, keys and tender names.
- `popcore_app/tests/test_clover_sandbox_probe.py`: the connector's Clover calculation, settle sequence, refusals, delete and single-order reads.
- `popcore_app/tests/test_clover_checkout.py`: import, device snapshots, cash, splits in either order, overpaid and take back, card-only in the background, write-back failure and retry, changes and refunds after the sale, cancel and delete, claim and release, closing blockers and the migration; since 2026-10-27 also cash rounded to 5¢ (and pay mode never writing more than Clover's total), a paid order with no items, an order that cannot be imported holding closing until it imports or is resolved, and orders from before the store went live.
- `popcore_app/tests/test_checkout_blind_boxes.py`: blind boxes over half a set answered before payment on manual and Clover orders, kept when Clover's items change, and answered later on a waiting sale or Record sale.
- `popcore_app/tests/browser/check_checkout_focus.py`: real Flask routes with a fake Clover at phone width. Covers automatic pick-up, the cash price, a split with card, cancel, find by ID, retry, and a POPCORE order sent to Clover with the device steps shown and the card payment landing on it.
- Sending to Clover: `test_clover_sandbox_probe.py` `ConnectorCreateTests` (one taxed order per key, replay, lost connector memory, another POPCORE copy's reference, discount, refusals that write nothing, orders changed or paid on the device, writer key and body checks) and `test_clover_checkout.py` `SendToCloverTests` (card on Clover records the exact designs on the same checkout and moves their stock, cash only labels Clover, refusals, Retry and Stop, a lost reply joined by reference, agreed discount, paid orders not sendable).

None of these proves live Clover, the Clover devices, Auth0 or production.

Live sandbox, 2026-10-20, on the Flex emulator with the hosted connector and the owner's local database. Payments were recorded through POPCORE's own receive path under the local test identity `local|claude-smoke`; its temporary Midtown grant was removed afterwards.

- `V6N6W4ZV6R1HC`: the device order reached POPCORE within seconds, unclaimed, with both lines matched. Cash was priced at $42.00 instead of $42.59. In 2.4 s Clover showed it PAID at $42.00, with a Cash discount of −$0.52, a Cash payment keyed `popcore-mac-1-1` carrying $4.83 tax, reference `POPCORE-1`, and both items at their original prices. Register cleared by itself.
- `T5TZ2XXJHQMD0`: Pay → ⋮ → Split Custom Amount → $22.00 on the device, with E-transfer standing in for card. The device showed $20.59 remaining. POPCORE asked for $20.00 and recorded it. The first write-back failed because Clover refuses a new external reference on an order that already has a payment; the connector now labels such orders through the title and payment notes. The retry left Clover PAID at $42.00: Rounding −$0.52, $22.00 E-transfer ($2.53 tax) and $20.00 Cash ($2.30 tax). The device returned to an empty Register.
- After later Clover reads, both orders stayed completed and showed no review. Stock moved once per sale.
- The owner's own run the same morning found two things. Items after the first appeared 8–20 seconds late, because the Android companion's six-hour sync session had ended at 07:44 and only Clover's own cloud sync was left; tapping **Start device sync** restored it. And the owner chose to leave orders paid outside Clover open there, labelled with the date and time, which is now the default.

Live sandbox, 2026-10-26, **Send to Clover for card**: the connector change was deployed to the hosted sandbox connector (backup `/opt/popcore-clover-sandbox/clover_sandbox.py.before-send-20261026`). POPCORE ran its real app and routes on a fresh database (`.local/clover-send-rehearsal/`, Midtown, design `PM0209-01` 小王子 The Businessman with 3 in stock and a toy `TT0001` with 5) under the local test identity `local|claude-rehearsal`, instance `rehearsal`; only the Auth0 token check was replaced, as in the unit tests. The owner's database and production were not touched.

- What the sandbox taught: Clover keeps at most 12 characters of external reference (`Invoice ID cannot exceed 12 characters`), hence the short form; a tax rate inside a line needs its `name`; the bulk line items request answers with a bare list; the sandbox merchant has no tax rate flagged default, hence matching the rate by percentage. Custom lines with the 13% rate are taxed exactly as POPCORE priced them (device: subtotal $55.39, tax $7.20, total $62.59), and Chinese names show on the device.
- A REST-created `open` order shows in the device's **Orders** app, not on Register's current-order screen. Its detail shows **Order #** (title), **Invoice Number** (external reference) and **Add Payment**.
- `N514PMBWZ3752` (checkout `MT-20261026-001`, 2× `PM0209-01` at $17.70 and 1× `TT0001` at $19.99): sent once; a second send returned the same order and Clover's feed showed no new order in POPCORE. Paid on the emulator with **Add Payment → E-transfer** for the full $62.59, because the emulator has no card reader and no manual card entry; that tender takes the same Clover-payment path as card. POPCORE completed `MT-20261026-001` by itself: sale dated 2026-10-26, source reference `MT-20261026-001`, lines 2× the design and 1× the toy, collected $62.59, allocated; stock `PM0209-01` 3 → 1 and `TT0001` 5 → 4; nothing written back. Clover shows it PAID.
- `J9CTRDJ93YMKA` (checkout `MT-20261026-002`, same lines): sent, then paid in POPCORE with cash at the automatic cash price $61.00 (Cash discount −$1.59). POPCORE recorded the sale and only titled the Clover order `Paid 2026-10-26 03:05`; it stays OPEN on Clover with no payment. Only one design was left, so this sale waits for stock allocation, POPCORE's usual rule for an already-paid sale it cannot cover.
- The 2026-10-26 probe order `WDWV5EAE25A6E` was deleted through the connector.
- Still not proven: a real card tap (the emulator cannot take one) and the owner's own hands-on run.

## Not covered

The production Clover app and merchant, deploying POPCORE, distributing the Android companion (needs Clover's APK approval), and real opening counts. Downtown and Markham cannot take Clover orders until their openings are verified (the existing inventory rule).
