# Clover device sync for checkout

Status: locally implemented after user approval; the Android debug APK built and installed on the sandbox emulator, and its Clover listener registered. Deployment, pairing, order callback verification, and timed end-to-end verification are pending.

## Measured problem

The 2026-09-28 sandbox test used order `DFBK2GASNENFE`. One authorized ADB tap added the seventh Taxable Item #2 and changed the register total from CAD 135.53 to CAD 158.12.

| Boundary | UTC time | Evidence |
| --- | --- | --- |
| Tap submitted on Mac | 19:26:44.105 | Timestamp around the ADB input command; command completed at 19:26:44.240 |
| Clover engine emitted the line item and total update | 19:27:03.852–03.860 on device clock | `OrderBatchBuilder` emitted `BulkLineItemV2Event`, `OrderDataUpdateEvent`, and `InactivityTimeoutEvent` |
| Clover reported new line-item creation | 19:27:05.000 | Cloud line item `010AK09M0E21R` |
| POPCORE stored the updated snapshot | 19:27:05.343 | Isolated checkout database `seen_at` |

Tap to POPCORE storage was 21.24 seconds. The device clock was about one second behind the Mac when checked, so the device-log interval is approximate. The cloud-to-storage difference was 0.343 seconds; the phone rendering timestamp was not captured. This demonstrates batching before cloud availability, not an end-to-end latency guarantee for any proposed replacement.

The installed probe was an older copy with blocking tender lookups. Normal probe reads took 0.322–0.385 seconds in four samples. A separate higher-frequency diagnostic encountered HTTP 429 responses. The local fix shares paced reads, propagates Retry-After, and preserves snapshot age; it cannot remove the device's batch delay.

## Selected direction

Use a read-only Android companion on the Clover device for immediate order snapshots. Keep the existing Clover cloud feed for reconciliation and confirmed payments. Polling faster or relying only on cloud webhooks cannot expose a scan before the device uploads it.

The [official Clover SDK](https://clover.github.io/clover-android-sdk/clover-android-sdk/com.clover.sdk.v3.order/-order-v31-connector/-on-order-update-listener2/index.html) provides callbacks for created/updated/deleted orders and added/updated/deleted line items. These callbacks are the proposed trigger, followed by a local `OrderConnector.getOrder` read. First verify on the emulator that the local snapshot includes up-to-date totals, discounts, taxes, and item quantities; unavailable money fields must remain unavailable rather than being estimated as confirmed totals.

## Sandbox implementation scope

1. A small companion uses the official Clover SDK and Read Orders permission. A visible service status shows whether it is connected. It does not initiate payment, edit Clover orders, or modify inventory.
2. After a local order callback, read the complete current snapshot. Coalesce a burst for at most 100 ms, with one send in flight and the newest pending snapshot replacing older pending snapshots. Each scan retains its distinct Clover line-item ID; never increment quantities from delivery count.
3. Send an allowlisted snapshot over HTTPS to a new endpoint on the existing sandbox probe: order ID, currency, current supported money fields, line-item IDs and quantities, and discounts. Exclude customer details, card data, OAuth tokens, and arbitrary raw SDK objects.
4. Use a separate revocable bridge credential bound to the configured sandbox merchant/device. The credential is not the probe administrator password or a production credential. Keep it out of source, APK resources, logs, URLs, and frontend code. Pair only the authorized emulator during this rehearsal.
5. Persist accepted snapshots in the isolated sandbox database. Enforce a persisted monotonic sequence for this paired device and idempotent retries. Atomically replace an order snapshot, so a delayed request cannot revert newer quantities or resurrect a removed line item.
6. The existing checkout adapter reads the newest device snapshot directly. Cloud snapshots must not overwrite a newer device snapshot while Clover is still catching up. Match order IDs and complete line-item IDs/amounts before treating cloud data as reconciled. Keep cloud-confirmed payment facts and completion rules separate from provisional device state.
7. Detect loss of the companion connection and explicitly mark its unreconciled state stale. Do not silently replace seven current device items with six old cloud items. Reconnect must send the latest complete state before restoring live guidance.

## Verification and release gate

- Prove the local SDK event/read path on the emulator before integrating payment guidance.
- Test duplicate delivery, out-of-order delivery, burst scans, item removal, cloud catch-up, credential rejection, and reconnect without quantity regression.
- Measure device action, server acceptance, and browser rendering for at least ten additions and a three-scan burst. Report median, p95, maximum, and failures. Target p95 at or below three seconds on a working connection; do not claim this until measured.
- Verify that paid/completed status still requires the existing matching confirmed Clover payment evidence.
- Produce a reviewable debug APK and server changes before requesting installation or live release approval. Production merchant approval, real hardware, device enrollment, and deployment remain separate from this sandbox test.

## Alternative

Keep only the cloud feed, apply the pacing fixes, and accept that the observed device batching can exceed three seconds. This is simpler but does not satisfy the requested latency target during scanning.
