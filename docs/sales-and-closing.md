# Sales and closing development runbook

Build 4 records individual completed/manual sales, their actual tender components, private payment evidence, inventory allocation, and one reviewed closing snapshot per store day. Clover remains the payment system. This workflow does not process payments, calculate discounts or taxes, issue processor refunds, import live droplet data, or deploy changes.

## Sales and inventory

- Staff use **Enter Sale** with one explicitly selected store. `/sales` remains the manager-only legacy aggregate report.
- A draft retains its exact source identity, product and money inputs. Posting freezes line names, forms, units, quantities, prices, taxes, and collected cents.
- A planned sale with insufficient stock remains a draft and posts nothing. An already-paid sale retains the financial fact with `allocation_status=pending`; a scoped manager resolves it later.
- Each sale allocation posts one immutable inventory document. Request keys are retained across uncertain responses, and replay reads the current sale without a second deduction.
- Random-box sales over half a reviewed set require an explicit opened-set or sealed-set selection. Opening and consumption share the sale transaction; retained units remain linked to their opened set.
- Source links require exact identifiers and manager review. Summary reconciliation records a comparison only. It never fabricates receipts, tender amounts, or stock movements from legacy quantities.
- Monetary corrections and recorded refunds are append-only payment events. Physical returns use a separate reviewed inventory receipt and cannot exceed the original allocated quantity.

## Handwritten daily reports

The legacy sales log imports one store and date at a time. `卡机汇总` contains POS quantities. `随手记汇总` (including the historical spelling `随手机汇总`) contains non-POS quantities (cash, e-transfer, WeChat Pay, or Alipay); its historical `qty_cash` column does not identify the tender. `现金:595/601.5` means physical cash actually received 595.00, expected 601.50, a shortage of 6.50. These amounts are stored separately in integer cents, never calculated from product prices.

Employee-discount and sold-display entries are already included in the two sales summaries. Their original annotations are saved without extra quantities. Claw-machine entries describe prizes won from separate stock: the report preserves the timestamps and text without recording a sale or changing inventory. Claw balances still require the reviewed inventory workflow. `入娃娃机` is saved separately as incoming claw stock. `入店display` / `出店display`, `入display` / `出display`, and stock lines marked `(display)` preserve opened-display movements as annotations, without consuming sealed stock again. A mixed incoming line such as `Echo：7+（display）*1` retains seven sealed units as a reviewed stock receipt and the original line as a separate opened-display annotation; the display unit does not consume sealed stock. Both counts must be explicit positive integers; malformed mixed expressions remain blocked for correction. Within the non-POS summary, `娃娃机*2` records e-transfer exchanged for equal physical cash; the marker does not specify a monetary amount or a product sale. It does not end the sales section. Receipt quantities such as `12*1` are 12 units; `12*3➕8` means three packs of 12 plus eight loose units (44 total), with no second multiplication by the catalog pack size. A plain `Product*2` or `Product:2` receipt means two individual units. `Peach riot power chords 12*1` means one pack of 12 (12 units); the final letter in a plural name is not a series marker. Only complete `s`, `ver`, `version`, or `series` suffix tokens protect the following series number from being mistaken for a pack size. Stock sections require explicit review and remain blocked in authoritative mode; a report with stock history cannot be replaced or deleted through the legacy report routes.

Product matching auto-confirms a unique exact identity or an unchanged, previously reviewed report choice. Successful submissions remember the exact product name and its notes together, so the same reviewed variant can be reused on later reports without teaching a broader catalog alias. Typography and case are normalized; meaningful letters, variant clues and notes remain part of the key. Conflicting choices or changed catalog identity require fresh review. Unseen fuzzy matches, tied exact names, malformed quantities and unknown sections still require review, and remembered choices never bypass quantity or stock review. Classifications of unfamiliar headings are saved in the same successful transaction and reused for that exact heading. Previewing, abandoning or failing to save a report teaches nothing. The report importer uses rule parsing and has no AI-provider option. Commas, semicolons and spaces after explicit quantities separate items while preserving names and parenthetical notes. Missing quantities remain blank for review; they never default to one. Optional LLM output cannot replace rule-parsed entries with changed names, quantities, or missing duplicates. Every active row must be resolved or explicitly removed before saving. Multi-date reports and invalid cash totals must be corrected before submission. Report metadata and quantities save in one transaction; summary rows never fabricate individual receipts or payment records.

## Payments and evidence

Cash, card, e-transfer, WeChat Pay, and Alipay stay separate. Amounts are integer cents; null means unknown. Split tenders sum their actual components, and any difference from the collected total remains visible. Only verified, known cash enters drawer arithmetic.

Evidence accepts one still JPEG, PNG, or WebP up to 10 MiB and 40 million pixels. The server decodes and re-encodes it, strips metadata, assigns an opaque filename, and stores it under `popcore_app/uploads/payment_evidence`, which is ignored by Git. Evidence is served only through its authenticated endpoint with private no-store caching. Staff can read attachments they uploaded; scoped managers can review them. Evidence review never verifies the payment itself.

Back up the SQLite database before its referenced evidence files, then copy the private evidence directory and record a manifest of object IDs and sizes. After restore, verify every database reference exists. A file without a database row is a private orphan: quarantine it for review rather than attaching or deleting it automatically.

## Closing

There is one closing session per store and local business date. Staff declare sales intake complete, resolve receiving/restock work, save explicit opening and retained coin amounts, enter every bill and coin denomination count, and complete required hot-item counts. The application uses a fixed $650 bill float plus the explicitly entered coins.

Expected drawer cash is opening cash plus verified cash receipts and paid-ins, less recorded cash refunds, payouts, and prior removals. Card and electronic tenders never enter this calculation. A float shortfall remains visible and blocks closing.

Submission captures a source token over the session, sales, payments, evidence states, cash facts, receipts, deliveries, counts, and balance versions. Any relevant change makes sign-off stale. A manager must refresh, review every named exception with a reason, and sign off. Closing stores one immutable snapshot and one linked cash-removal event. Replaying sign-off returns the original result. Later paid facts create linked adjustments and do not rewrite the snapshot.

## Local verification

Run from `D:\dev\POPCORE` with temporary files and browser binaries on D::

```powershell
$env:DISABLE_SCHEDULER = '1'
$env:TEMP = 'D:\dev\POPCORE\.local\tmp'
$env:TMP = $env:TEMP
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\playwright-browsers'
.\.venv\Scripts\python.exe -m unittest discover -s popcore_app\tests -v
.\.venv\Scripts\python.exe popcore_app\tests\browser\check_store_day.py
Set-Location popcore_app\frontend
npm test
npm run build -- --outDir ../../.local/frontend-build-build4
```

The tests use isolated synthetic databases and blocked external requests. The browser check covers staff sale entry, private evidence capture, Closing, viewer denial, keyboard entry, 390/768/1440-pixel layouts, and 200% zoom. Real Auth0 identities, store opening counts, Clover reconciliation, coin policy, exception policy, evidence retention/backup destination, and a representative store pilot remain launch decisions.
