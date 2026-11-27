# Clover production app submission (draft)

Status (2026-10-20): draft for the owner's review. Nothing here has been created, submitted or sent. The production developer account was verified on 2026-10-19 and has no apps yet. Creating the production app, submitting it and emailing Clover each need the owner's go-ahead.

The production app cannot be copied from the sandbox one. It has to be created again in the production Developer Dashboard, because sandbox permissions and settings do not carry over. Clover then approves it like any other app, in three reviews:

1. A functional review: the demo video and its description, the permissions, the REST configuration, the webhooks and any APK. Clover's team installs the app on a production test merchant to test it.
2. A legal review of a privacy policy and terms of service. These are required for public apps and for apps not published on the App Market, so a private app needs them too.
3. A listing review: name, icon, benefits, categories and support details.

The Android companion (Device Sync) is a separate APK approval, which can only start after the app itself is approved.

Code facts below were checked against commit `1239b66b` on `codex/clover-checkout`. Clover facts come from the pages listed under [Sources](#sources), read on 2026-10-20.

## What the submission needs

| Clover asks for | Section | State |
|---|---|---|
| App type: private | [Dashboard steps](#dashboard-steps-later-with-the-go-ahead) | Ready |
| Permissions, each justified | [Permissions](#permissions) | Checked against the code. One sandbox run with exactly this list is still to do |
| REST configuration | [REST settings and webhooks](#rest-settings-and-webhooks) | Needs the production connector's address |
| Webhooks | [REST settings and webhooks](#rest-settings-and-webhooks) | Needs the production connector running |
| Functional video and description | [App summary](#app-summary-for-clover-reviewers), [Demo video script](#demo-video-script) | Script ready, not recorded |
| Privacy policy and terms of service, at public URLs | [Legal drafts](#legal-drafts) | Drafts. Need legal review, business details and a place to publish them |
| Listing: icon, tagline, benefits, categories, support | [Listing text](#listing-text) | Text drafted. Needs an icon and support contacts |
| APK for Device Sync | [Open questions](#open-questions-and-decisions) | Not ready: only a sandbox build exists |

## App summary for Clover reviewers

Paste this as the app description and as the description that goes with the demo video. The listing uses the same name as the sandbox app, POPCORE Order Sync.

> POPCORE Order Sync connects one Clover merchant, our own store, to POPCORE, the store's own inventory and sales record. It is a private app for that merchant only.
>
> Clover stays our point of sale. Staff scan items and take card payments on the Clover device as usual. POPCORE reads each Clover order and records it as a sale with its items, payments and the stock it uses. Card payments are taken and processed only by Clover. A sale paid in full on Clover needs nothing written back.
>
> Some customers pay with cash, Interac e-Transfer, WeChat Pay or Alipay. The store accepts these through its own accounts, outside Clover and outside this app. Once such a sale is fully paid and recorded in POPCORE, the app titles the Clover order "Paid <date> <time>" and adds the same line to the order note, so staff can see the order is settled. The order stays open on Clover, or partly paid when part of it was paid by card there. The app adds no payment, tender or discount, and it never changes items, prices or tax.
>
> When staff cancel an unpaid order in POPCORE, the app deletes that order on Clover so both systems match. It never deletes an order that has a payment.
>
> The app does not process payments, receive card data or read customer records. It reads orders, payments, tenders, item references and the employee on each order. Its only writes are an order's title and note, and deleting unpaid orders the store cancelled. It reads at most one order list per second and backs off when Clover asks it to. An optional Android companion, which reads orders only, will be submitted separately.

## Permissions

### Requested (label mode, `CLOVER_WRITEBACK=label`, the default)

These are paste-ready one-line justifications.

| Permission | Why |
|---|---|
| Read Merchant | To read the tender list, so a card payment can be told from a cash one. |
| Read Employees | To show which Clover employee rang up each order. |
| Read Inventory | To match order lines to our products by Clover item ID and read their tax rates. |
| Read Orders | To record each Clover order as a sale in POPCORE, and to receive order webhooks. |
| Read Payments | To record the card payments, refunds and credits Clover took, and to receive payment webhooks. |
| Write Orders | To title orders paid in POPCORE "Paid <date> <time>" and to delete unpaid orders we cancel. |

Not requested: Write Payments, Read or Write Customers, Write Merchant, Write Inventory, Write Employees, and online payments (Ecommerce). Clover treats an app that asks for a permission it does not use as broken, so every extra permission is a risk.

### How this was checked

POPCORE calls only five connector routes (`popcore_app/clover_checkout.py`):

| POPCORE call | When |
|---|---|
| `GET /orders` | `sync`, every checkout screen poll and the 5-second background job |
| `GET /orders/<id>` | `refresh_one`: while an open order is on screen (at most every 3 seconds), before money is recorded, and for open orders no longer in the latest 20 |
| `GET /orders/scan` | `sync_day`, at store-day closing |
| `POST /orders/<id>/label` | `write_back`, after a sale paid in POPCORE (pay mode calls `/settle` instead) |
| `DELETE /orders/<id>` | `_cancel`, and later retries of a cancel Clover missed (`follow_up`) |

The Clover calls behind those routes (`scripts/clover_sandbox.py`):

| Clover call | Used by | Permission |
|---|---|---|
| `POST /oauth/v2/token`, `POST /oauth/v2/refresh` | Connecting and keeping the token fresh | None |
| `GET /v3/merchants/{mId}/orders` with `expand=lineItems,discounts,payments,refunds,credits,employee` | `/orders` (latest 20), `/orders/scan` (one day, by `createdTime`) | Read Orders; Read Payments for payments, refunds and credits; Read Employees for the employee |
| `GET /v3/merchants/{mId}/orders/{orderId}`, same expansion | `/orders/<id>`, and the label and delete routes before they write | As above |
| `GET /v3/merchants/{mId}/orders/{orderId}/line_items?expand=taxRates,modifications,discounts` | Same routes | Read Orders; Read Inventory for tax rates |
| `GET /v3/merchants/{mId}/tenders` | Naming a payment's tender, cached for 10 minutes | Read Merchant |
| `POST /v3/merchants/{mId}/orders/{orderId}` with `title` and `note` | `/orders/<id>/label`. If Clover refuses the note, the title alone is written | Write Orders |
| `DELETE /v3/merchants/{mId}/orders/{orderId}` | `DELETE /orders/<id>`, only when the order has no successful payment | Write Orders |

The webhook and the companion add these:

- Orders webhooks need Read Orders, and Payments webhooks need Read Payments.
- The companion reads orders on the device with `OrderConnector.getOrder`, which needs Read Orders.

Supporting evidence:

- `popcore_app/tests/test_clover_sandbox_probe.py` passed all 24 tests on 2026-10-20. The label test asserts that the route makes exactly one Clover write, `POST .../orders/{orderId}` with `title` and `note`, and that the order stays `OPEN` with no payments. The delete test asserts that an order with a payment is refused and nothing is written.
- Clover's permissions page gives `POST /v3/merchants/{mId}/orders/{orderId}` as its example of Write Orders, and `POST /v3/merchants/{mId}/orders/{orderId}/payments` as its example of Write Payments.

Two limits remain:

- Clover's API reference does not list a permission for each endpoint. The connector's tenders call sits under Merchants and tax rates sit under Inventory, so Read Merchant for tenders, Read Inventory for `taxRates` and Read Employees for `expand=employee` are inferred from that layout. Any one of them, if missing, could make Clover refuse a whole order read.
- This exact list has not run against Clover. The sandbox app also has Write Payments, because it runs pay mode too ([clover-checkout.md](clover-checkout.md) still lists it). Before recording the video, remove Write Payments from the sandbox app, then uninstall and reinstall it on the test merchant (Clover applies permission changes only on reinstall). Then run one read, one label and one delete.

### What pay mode would add

`CLOVER_WRITEBACK=pay` calls `POST /orders/<id>/settle` instead of `/label`.

- It adds Write Payments, for `POST /v3/merchants/{mId}/orders/{orderId}/payments`: one external payment per POPCORE tender, keyed by `externalPaymentId`.
- It uses Write Orders more heavily: it deletes and adds order discounts (`/discounts`), sets the order `total`, and sets `title` and `externalReferenceId`.
- The merchant would need custom tenders named E-transfer, WeChat and Alipay.
- Clover would then show money taken outside Clover as Clover payments. That invites the question below about payments outside Clover.

If the production app has no Write Payments, `CLOVER_WRITEBACK` must stay `label`. In pay mode the connector writes the discount and the new total before the payments. A refused payment would leave the Clover order discounted but unpaid, and every retry would fail the same way.

## REST settings and webhooks

From [clover-sandbox.md](clover-sandbox.md) and [clover-checkout.md](clover-checkout.md), with the production host still to be chosen:

| Dashboard field | Production value | Sandbox today |
|---|---|---|
| App type | Private app, platform Web app (REST clients) | REST Clients / Web |
| Site URL | `https://<connector host>/clover-sandbox/` | `https://popcore.store/clover-sandbox/` |
| Alternate Launch Path | `/clover-sandbox/connect` | Same |
| Default OAuth Response | Code | Code |
| CORS Domain | Blank (server-side client) | Blank |
| Webhook URL | `https://<connector host>/clover-sandbox/webhooks/<WEBHOOK_PATH_KEY>`. Private: copy it from the connector's admin page, never from a screenshot | Same pattern |
| Webhook events | Orders and Payments only | Orders and Payments |

- The OAuth redirect is not a dashboard field. The connector sends `redirect_uri=<PUBLIC_URL>/callback`, which Clover requires to be a subpath of the Site URL. The Alternate Launch Path must use the Site URL's domain.
- With `CLOVER_ENVIRONMENT=production` the connector uses `https://www.clover.com/oauth/v2/authorize`, with `https://api.clover.com` for tokens, refresh and the API. These match Clover's North America hosts, which cover Canada.
- After approval, the private install link is `https://www.clover.com/oauth/authorize?client_id=<APP ID>`.
- The connector's path is fixed: `PREFIX = '/clover-sandbox'`, and it refuses a `PUBLIC_URL` that does not end in `/clover-sandbox`. A production connector therefore cannot share `popcore.store/clover-sandbox/` with the sandbox one. Reviewers will also see "sandbox" in the production URL and in the connector's login prompt. See [Open questions](#open-questions-and-decisions).

Webhook setup, as done in the sandbox:

1. Start the connector without `CLOVER_WEBHOOK_AUTH`.
2. In the dashboard, enter the webhook URL and choose Send Verification Code. The connector's admin page then shows the code.
3. Paste the code into the dashboard, choose Verify, and save.
4. Subscribe to Orders and Payments.
5. Copy Clover's auth code into `CLOVER_WEBHOOK_AUTH` and restart the connector. Until then it accepts only the verification challenge.

A production connector needs its own process, configuration file and data directory, and fresh secrets. Never reuse the sandbox ones.

- Connector `config.json`:
  - `CLOVER_ENVIRONMENT=production`, `PUBLIC_URL`, and `DATA_DIR`.
  - `CLOVER_APP_ID` and `CLOVER_APP_SECRET` from the production app.
  - `CLOVER_MERCHANT_ID`: the store's production merchant.
  - `SESSION_SECRET`, `ADMIN_PASSWORD`, `WEBHOOK_PATH_KEY` and `POPCORE_WRITER_KEY`, each at least 32 characters.
  - `CLOVER_WEBHOOK_AUTH`, after verification.
  - `DEVICE_BRIDGE_ID` and `DEVICE_BRIDGE_SECRET`, only once the companion is approved.
- POPCORE environment:
  - `CLOVER_CONNECTOR_URL`, `CLOVER_CONNECTOR_PASSWORD` and `CLOVER_CONNECTOR_WRITER_KEY`.
  - `CLOVER_MERCHANT_ID` and `CLOVER_STORE_ID`.
  - `CLOVER_ENVIRONMENT=production` and `CLOVER_WRITEBACK=label`.
  - `CLOVER_IMPORT_SINCE`, set to the first production day.

## Dashboard steps (later, with the go-ahead)

1. Sign in to the Global Developer Dashboard and switch the Sandbox toggle to production. Choose Create new app:
   - App Name: POPCORE Order Sync.
   - Country: Canada.
   - App type: Private app. Not "Payment app", because POPCORE takes no payments through Clover.
   - Platform: Web app only. Add Android when a production companion build exists.
2. Requested Permissions: exactly the six above.
3. REST Configuration and Webhooks: the values above.
4. Merchant Availability: select only the modules the app uses, and check that the store's Clover plan includes them.
5. Pricing & Distribution: no paid tiers. Private apps are not billed through the App Market, and Clover discusses pricing during review. Answer: free, internal use only.
6. Market Listing: the [listing text](#listing-text), an icon the owner has the rights to, at least one functional and one vertical category, support contacts, and the EULA and Privacy Policy URLs.
7. Attach the demo video and its description, then submit. The status becomes Under Review.
8. After approval:
   1. Install the app on the store's merchant with the private link.
   2. Connect it from the connector's admin page.
   3. Verify the webhook.
   4. Check one real, low-value sale end to end before the store relies on it.

## Listing text

- Tagline (255 characters at most): Connects our own store's Clover orders to POPCORE, our inventory and sales record.
- Merchant benefits (at least 3, 100 characters each at most):
  1. Every Clover sale lands in POPCORE with its items, so stock counts stay right.
  2. Orders settled in POPCORE are titled on Clover, so no one charges them twice.
  3. Cancelling an unpaid order in POPCORE removes it from Clover too.
  4. Day close checks every Clover order of the day before the store closes.
- Description: the [app summary](#app-summary-for-clover-reviewers).
- Categories: the closest inventory or reporting category, and Retail as the vertical. Use the names the dashboard offers.
- Support: [phone], [hours], [email], [website].

## Demo video script

Clover asks for a video that walks through the app's workflow and shows how it helps the merchant. Its example is linked under [Sources](#sources). Aim for six to eight minutes, narrated or captioned. Show the Clover device and POPCORE side by side, so each Clover change appears next to its cause.

### Before recording

- Record in the sandbox:
  - The **POPCORE Canada Sandbox** merchant.
  - The Flex emulator.
  - The hosted sandbox connector.
  - A POPCORE copy with test products. `scripts/setup_clover_local.py` makes one: it backs up the database first and is a dry run without `--apply`.
- Set the sandbox app's permissions to exactly the six above, then uninstall, reinstall and reconnect it. Rehearse every scene once. This is also the missing check of the permission list.
- Configure POPCORE with `CLOVER_WRITEBACK=label`, the writer key set, and background sync on. Start Device Sync on the emulator so items appear quickly.
- Never show any of these:
  - The connector's admin password.
  - The "Clover dashboard settings" block of its admin page, which shows the private webhook URL and the verification code.
  - The App Secret, tokens or the writer key.
  - An address bar while it holds the OAuth callback's `code` and `state`.
  - `.env` or `config.json` files.
  - Real staff or customer data, or real payment photos.

### Scenes

| # | Time | Do | Say | Shows |
|---|---|---|---|---|
| 1 | 0:00 | Title card, then the POPCORE home screen. | POPCORE is our store's own inventory and sales system. POPCORE Order Sync is a private app for our one Clover merchant. Clover stays our point of sale and card terminal. The app records Clover orders in POPCORE. It does not process payments. | Purpose |
| 2 | 0:30 | Sandbox Merchant Dashboard → the app → Open. The owner signs in to the connector, approves on Clover's page, and the connector shows "Connected to the configured merchant" (crop out its settings block). | The owner connects once. The app uses Clover's OAuth code flow, and the tokens stay on our server. It asks for read access to tenders, employees, inventory, orders and payments, and write access to orders only. | Alternate Launch Path, OAuth, permissions |
| 3 | 1:15 | Scan two items on the device. A POPCORE checkout screen on a phone opens the order by itself, both lines matched to products. On the device: Pay → Card, test card. POPCORE records the sale; open it to show the lines, the verified card payment and the stock movement. | Card is taken by Clover only. POPCORE reads the payment from Clover and records the sale and its stock. | Read Orders, Payments, Inventory, Employees, Merchant |
| 4 | 2:15 | Refund that card sale on the device. POPCORE flags the sale: "Clover recorded a refund, void or credit on this order." | Refunds stay on Clover. POPCORE flags the sale so a manager reviews it at closing. | `REFUNDED` handled |
| 5 | 2:45 | Scan an item. On POPCORE the Cash button already shows the cash price. Collect, tap Received. In the Merchant Dashboard's Orders: the order is titled "Paid <date> <time>", its note has the same line, it is still Open and its total is Clover's. On the device: Review Order → Save. | For cash, POPCORE records the payment. The app writes only the order's title and note, so staff can see it was paid. It adds no payment or discount on Clover, and the total stays Clover's. | Write Orders |
| 6 | 3:45 | New order, choose WeChat Pay. POPCORE shows the CNY amount; tap Received and add a test payment photo. Clover shows the same title. | The store accepts WeChat Pay, Alipay and e-Transfer through its own accounts, outside Clover and outside this app. POPCORE only records them. | No payment written |
| 7 | 4:30 | Split payment in POPCORE, type the card part. On the device: Pay → ⋮ → Split Custom Amount → the amount → Card. POPCORE shows the rest; collect cash, tap Received. Clover shows the order partly paid with the title; on the device tap Pay more later. | Clover keeps the card part. POPCORE records the rest and titles the order. | Mixed tenders |
| 8 | 5:30 | Scan an item, then Cancel this order in POPCORE. The order is gone from Clover's Orders list. | The app deletes an order only when staff cancel it in POPCORE and it has no payment. | Write Orders (delete) |
| 9 | 6:15 | POPCORE store-day closing: it scans the day's Clover orders and blocks closing while one is still open. | At closing, POPCORE reads every Clover order of the day, so nothing is missed. | Read Orders |
| 10 | 6:45 | Closing card: the six permissions and what the app never does. | The app never processes payments, receives card data, reads customers, or changes items, prices or tax. | Summary |

Wording for the narration:

- Call POPCORE's price for cash a cash discount. Card always pays Clover's price, and Clover forbids card surcharges.
- Say "records" for money taken outside Clover, never "processes".

## Legal drafts

Both texts follow the structure of Clover's legal templates. They are rewritten, not copied, for one Canadian merchant that is also the developer.

Replace every [bracketed] item before publishing, then publish both at stable public URLs, for example on popcore.store. The listing asks for both URLs.

### Privacy policy

> **DRAFT for the owner's legal review. This is not legal advice.**
>
> Two changes from Clover's template:
>
> - The template casts the developer as the merchant's processor. Here we are both, and the draft says so.
> - The template's European (GDPR) and California (CCPA) sections are left out, because the app serves one store in Ontario. Ask counsel whether to keep them.
>
> Remove this note before publishing.

**Privacy Policy for POPCORE Order Sync**

Effective date: [date]

[Legal business name] ("we", "us") runs [store name] and built POPCORE, our own inventory and sales system. POPCORE Order Sync is a private Clover app that connects our Clover merchant account to POPCORE. We do not offer it to other businesses. This policy explains what information the app handles and how we use it. Clover's own handling of information is described in Clover's privacy notice: [link].

**Information the app collects**

From our Clover account:

- Orders: order number, date and time, status, total, title and note. For each line: item name, Clover item ID, product code, price, quantity and unit. Also discounts and tax.
- Payments made on Clover: payment number, amount, tax and tip amounts, result, tender name (for example credit card or cash), and any refunds or credits. The app does not receive card numbers, cardholder names or other card data.
- Employees: the ID and name of the Clover employee on each order and payment.
- Our tender list: the names of the payment types set up in Clover.
- Change notices: the type and time of order and payment changes Clover sends us.
- Connection credentials: the access and refresh tokens Clover issues to the app.

From the Clover device, if the optional POPCORE companion app is installed: the items, discounts and total of open orders, and a random ID for the device.

Entered by our staff in POPCORE, not received from Clover:

- The payment type and amount received for sales paid outside Clover.
- For WeChat Pay and Alipay, the amount charged in Chinese yuan and the exchange rate.
- For Interac e-Transfer, WeChat Pay and Alipay, a photo of the payment confirmation. These photos can show the payer's name or account name.

The app does not read Clover customer records. It does not collect card data, customer contact details, location, or browsing or advertising data.

**How we use it**

- To record each sale, its items and its payments in POPCORE, and to keep our stock counts accurate.
- To show our cashiers what is owed and which Clover order they are working on.
- To mark Clover orders that were paid in POPCORE, and to remove unpaid orders we cancelled, so both systems agree.
- To close each store day, reconcile payments, and keep our accounting and tax records.
- To find and fix problems, keep our systems secure, and prevent errors and fraud.

We do not use this information for advertising or profiling, and we do not sell it.

**Who we share it with**

- Clover (Fiserv), our point-of-sale provider. The app sends order titles, notes and cancellations back to our own Clover account.
- Service providers that host or secure our systems, only to run them for us: [hosting provider and region], [Auth0, for staff sign-in].
- Our accountants, auditors and advisers, when needed.
- Authorities, when the law requires it, or to protect our rights, staff or customers.
- A buyer or successor, if our business is sold or reorganized.

**Storage, security and retention**

The information is stored on our servers at [provider, location]. [If outside Canada: it may be accessed by authorities there under local law.] Connections use HTTPS. Clover tokens and keys are kept in private files on the server, never in a browser or in the app's code. Only our staff with POPCORE accounts can see the information, limited by their role.

We keep sales and payment records, including payment photos, for [six years, the period Canadian tax rules generally require for business records; confirm], then delete them. Clover change notices and device order copies are kept for [period]. When the app is uninstalled, Clover stops accepting its tokens, and we delete them from our server.

**Your rights and choices**

Canadian privacy law gives you the right to ask what personal information we hold about you, to ask us to correct it, and to withdraw your consent, subject to legal and contractual limits. To make a request or a complaint, contact us below. You may also complain to the Office of the Privacy Commissioner of Canada. For information Clover holds, see Clover's privacy notice.

**Changes**

We will post any change here and update the effective date.

**Contact**

[Legal business name], [address], [privacy contact email], [phone]

### Terms of service

> **DRAFT for the owner's legal review. This is not legal advice.**
>
> Adapted from Clover's EULA template for a free, private app that the developer uses for its own merchant account. The template's fee, billing, monthly-term and New York law clauses are replaced. The liability cap is a placeholder.
>
> Remove this note before publishing.

**Terms of Use for POPCORE Order Sync**

Last updated: [date]

These terms are between [Legal business name] ("Developer") and the business that installs POPCORE Order Sync on its Clover merchant account ("Merchant"). The app is private. The Developer offers it only for Clover merchant accounts that [Legal business name] owns or operates. By installing or using the app, the Merchant accepts these terms.

1. **The app.**
   1. The app connects the Merchant's Clover account to POPCORE, the Merchant's inventory and sales system. It reads the Merchant's Clover orders, payments, tenders, item references and order employees. It titles orders recorded as paid in POPCORE, and it deletes unpaid orders the Merchant cancels in POPCORE.
   2. The Developer gives the Merchant a limited, non-exclusive, non-transferable right to use the app for its own internal business. Except as the law allows, the Merchant may not copy, modify, resell, share or reverse engineer the app. The Developer keeps all other rights.
   3. The Developer may update the app, interrupt it for maintenance, or stop supporting old versions. The Merchant provides its own devices, Clover service and internet connection.
2. **Payments.** The app does not process payments or handle card data. Card payments are taken and processed by Clover, under the Merchant's agreements with Clover and Fiserv. The Merchant accepts cash, Interac e-Transfer, WeChat Pay and Alipay payments through its own arrangements, outside the app, and POPCORE only records them. These terms do not change the Merchant's agreements with Clover or Fiserv.
3. **Fees.** The app is free. The Developer will not charge for it unless both parties agree to new terms.
4. **Term and ending.** These terms start when the app is installed. They last until it is uninstalled, or until either party ends them by notice. The Developer may suspend the app at once if it is misused, or to protect security or data. When the app is uninstalled, the Developer stops reading Clover data and deletes the app's Clover tokens. Sales records already in POPCORE remain the Merchant's business records.
5. **Confidentiality and data.**
   1. Each party keeps the other's confidential information private and uses it only for these terms, except where the law requires disclosure.
   2. The Developer uses data from Clover only to run the app and keep the Merchant's records, as described in the privacy policy at [link]. It does not sell personal information.
   3. The Developer may freely use suggestions for improving the app.
6. **Merchant responsibilities.** The Merchant will:
   - keep its Clover and POPCORE sign-ins safe;
   - make sure staff use the app as instructed;
   - tell the Developer promptly about errors or security problems;
   - follow Clover's terms.

   Sales paid in POPCORE stay open on Clover. The Merchant therefore uses POPCORE's day close, not Clover's reports, as its record of those sales.
7. **Disclaimers and liability.**
   1. The app is provided as is. The Developer does not promise that it will be uninterrupted or free of errors.
   2. To the extent the law allows, neither party is liable for indirect or consequential losses or lost profits.
   3. The Developer's total liability under these terms is limited to [amount].
8. **Privacy laws.** The Developer handles personal information under applicable Canadian privacy law. It uses that information only to provide the app, and helps the Merchant answer requests about it.
9. **Clover.** Clover and Fiserv are not parties to these terms and are not responsible for the app. Support for the app comes from the Developer at [contact].
10. **General.**
    - These terms are governed by the laws of Ontario and the federal laws of Canada that apply there, and the courts of Toronto, Ontario have jurisdiction.
    - The Developer may change these terms with notice, and continued use means acceptance.
    - If any term is invalid, the rest still applies.
    - The Merchant may not transfer these terms without the Developer's consent.
    - These terms are the whole agreement about the app.

Contact: [Legal business name], [address], [email], [phone]

## Draft email to Clover Developer Relations

Not sent. To `developer-relations@devrel.clover.com`, from the developer owner's account, after the owner reviews it.

> **Subject:** Private single-merchant app: production token, Write Orders review, APK approval
>
> Hello,
>
> I run [store name] in Ontario and built POPCORE, our own inventory and sales system. Our production developer account (Developer ID WK1HHVKCTYB78) was verified on 19 October 2026 and has no apps yet. In the sandbox, our app POPCORE Order Sync is installed on the test merchant POPCORE Canada Sandbox.
>
> Clover stays our point of sale and card terminal. POPCORE reads our Clover orders to record sales and stock, and it doesn't process payments. Some customers pay with cash, Interac e-Transfer, WeChat Pay or Alipay, which we accept outside Clover. For those sales, POPCORE titles the Clover order "Paid <date> <time>" and leaves it open. When we cancel an unpaid order in POPCORE, it deletes the Clover order. The integration is for our own merchant account only.
>
> Before we create the production app, could you help with three questions?
>
> (a) Production token. Your OAuth and tokens FAQ says an integration meant for one merchant can use a merchant-generated token. Other pages say merchant API tokens are for sandbox testing only and production needs OAuth. For our single-merchant internal integration, may we use a token generated on our own production merchant? Or do we need an approved private app with OAuth?
>
> (b) Review. As a private app we would request Read Merchant, Employees, Inventory, Orders and Payments, plus Write Orders. We would not request Write Payments or Customers. Write Orders only sets an order's title and note, and deletes unpaid orders we cancel. What review should we expect? Is it acceptable that orders paid outside Clover stay open on Clover with that title? And since our server accepts only our own merchant, how would your team like to test it?
>
> (c) APK approval. We also have a small Android companion, Device Sync. It reads open orders on our Clover device with the Clover Android SDK (Read Orders only) and sends their items to our server. Today it builds only the sandbox package ca.popcore.cloversync.sandbox, because ca.popcore.cloversync was refused as already in use. Can the APK be added to the same private app after it is approved? Must the production package name differ from the sandbox one? And what does APK review involve for a private app on one merchant's devices, and how long does it usually take?
>
> Pages referred to:
>
> - https://docs.clover.com/dev/docs/oauth-and-tokens-faqs
> - https://docs.clover.com/dev/docs/generate-a-test-api-token
> - https://docs.clover.com/dev/docs/basic-app-configuration
>
> Thank you,\
> [Name]\
> [Legal business name], [phone]

## Open questions and decisions

1. **Where the production connector lives.** The connector's path is fixed at `/clover-sandbox`. There are three options:
   - Run production on its own host name, for example `clover.popcore.store`. This needs no code change, but "sandbox" stays in the production URL.
   - Retire the sandbox connector and reuse its URL.
   - Make the path and the "sandbox" wording configurable first. This is a small code change, and the cleanest URL for reviewers.
2. **Payments outside Clover.** Clover's functional tests require all payments to go through Clover, Fiserv or its banking channels, and integrating another payment processor is a listed rejection reason. POPCORE processes none of these payments. It does, however, work out WeChat Pay and Alipay amounts in CNY and records the payments. The email raises this in (b). The owner may want an answer before recording the video.
3. **Orders left open on Clover.** Clover's tests cover open, paid and refunded orders, and expect payments to show correctly in the Orders app. In label mode:
   - A sale paid in POPCORE stays open on Clover.
   - Clover's reports do not count that money.
   - Cash taken that way is not in Clover's cash log.

   The video explains this. Pay mode would avoid it, at the cost of Write Payments.
4. **How reviewers test it.** Clover installs apps on its own production test merchant. The connector accepts only the configured merchant ID, and its pages, including the launch path, need the admin password. Asked in (b).
5. **Expanded fields.** Clover asks apps to expand at most three fields per call. The connector expands six on every order read (`lineItems,discounts,payments,refunds,credits,employee`). This works in the sandbox. If production enforces the limit or reviewers object, the reads must be split. That is a code change.
6. **Merchant token.** If Clover answers yes to (a):
   - The REST side might not need an approved app, but the connector would need a small change to use a fixed token instead of OAuth.
   - Webhooks and the APK still belong to an app. POPCORE also works without webhooks, which only make its next read immediate.
7. **The companion.** POPCORE works without Device Sync: without it, items arrive from Clover's cloud 8 to 20 seconds later. Submitting the web app first and the APK later is the smaller first step. A production build needs code changes before APK submission:
   - A new application ID.
   - Its production endpoint. The current default is `https://popcore.store/clover-sandbox/device-snapshot`.
   - New wording, since it says "sandbox" throughout.
   - All-day sync instead of the six-hour sandbox session.
   - V1 signing with the same keystore for every version.

   It has been tested only on the Flex emulator, not the store's Station Duo.
8. **Business details for the legal texts and listing:**
   - The legal name and address.
   - Privacy and support contacts.
   - Where the server is hosted.
   - How long records are kept.
   - Where to publish both documents.
   - An icon the owner has rights to.
9. **[clover-checkout.md](clover-checkout.md) still lists Write Payments** for the Clover app. That is true of the sandbox app, which also runs pay mode. Correct it when the production app exists. It was left unchanged here.

## Sources

Clover pages, read 2026-10-20:

- App approval: https://docs.clover.com/docs/developer-app-approval
- Functional review playbook: https://docs.clover.com/dev/docs/clover-functional-review-playbook
- Basic app configuration: https://docs.clover.com/dev/docs/basic-app-configuration. Covers sandbox permissions not transferring, test tokens not allowed in production, and unused permissions.
- Full app functionality tests: https://docs.clover.com/dev/docs/full-app-functionality-tests. Covers processing payments through Clover or Fiserv, order states, the cash log and surcharges.
- Example functional video: https://youtu.be/5HOemzBEezE
- Legal templates: https://docs.clover.com/docs/legal-templates, which links the privacy policy template (https://docs.clover.com/dev/docs/clover-privacy-policy-template) and the EULA template (https://docs.clover.com/dev/docs/clover-eulaterms-of-service-template).
- Permissions: https://docs.clover.com/docs/permissions
- Webhooks, including the event types and the read permission each needs: https://docs.clover.com/dev/docs/webhooks
- Private apps: https://docs.clover.com/dev/docs/private-apps
- Create a new app: https://docs.clover.com/dev/docs/gdp-create-new-app
- App settings: https://docs.clover.com/dev/docs/app-settings
- Market listing fields: https://docs.clover.com/dev/docs/managing-app-details
- Site URL, launch path and redirect rules: https://docs.clover.com/dev/docs/using-cors
- OAuth hosts: https://docs.clover.com/dev/docs/high-trust-app-auth-flow
- Tokens:
  - https://docs.clover.com/dev/docs/oauth-and-tokens-faqs
  - https://docs.clover.com/dev/docs/generate-a-test-api-token
- APK approval: https://docs.clover.com/docs/submitting-an-apk-for-approval
- Expansion limit: https://docs.clover.com/docs/expanding-fields
- API reference categories:
  - Tenders: https://docs.clover.com/dev/reference/paygetmerchanttenders, tagged Merchants.
  - Tax rates: https://docs.clover.com/reference/taxrategettaxrates, under Inventory.
