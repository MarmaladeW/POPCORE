# Clover Device Sync Implementation Plan

Status (2026-09-28): probe intake, checkout adaptation, and companion source are implemented locally. The Python suite (583 tests), frontend suite (39 tests), verification build, and Java queue check pass. The debug APK builds against the real Clover SDK 334, is installed on the sandbox emulator, and registers the local Clover listener. Pairing, order callback verification, and a timed device-to-phone test remain open. No release was made.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show Clover sandbox line-item changes in POPCORE within three seconds when the device and network permit it, without marking a sale paid from provisional device data.

**Architecture:** A read-only Clover Android companion listens for local order changes and sends complete, allowlisted order snapshots to the isolated sandbox probe. The probe stores one paired device's ordered snapshots, merges them with cloud data for checkout reads, and keeps payment completion based on the existing cloud-confirmed payment flow. The checkout adapter preserves the source and age of each snapshot.

**Tech Stack:** Android Java, official Clover Android SDK, Android platform HTTP and JSON, Flask, SQLite, Python unittest.

**Spec:** `docs/superpowers/specs/2026-09-28-clover-device-sync-design.md`

## Global Constraints

- Sandbox merchant and one paired emulator only; no POPCORE operational database writes.
- No customer, card, OAuth token, or raw SDK object sent by the companion.
- The companion reads orders; it never modifies orders, payments, or inventory.
- Device line-item IDs and quantities replace full snapshots; retries cannot increment quantities.
- Clover cloud payment evidence remains the only source for completed status.
- Keep source timestamps and staleness visible; no 1–3 second guarantee before a timed end-to-end test.
- Preserve existing uncommitted cloud-reader changes. Do not commit, push, install, or deploy without the applicable authorization.

## Review Focus

- Duplicate and out-of-order device deliveries must not regress item counts.
- Cloud data arriving behind a device snapshot must not erase new scans.
- Invalid money or absent local totals must not become confirmed payable amounts.
- A wrong credential, merchant, or device must be rejected before storage.
- A disconnected device must become stale even if an older cloud request succeeds.

### Task 1: Device snapshot intake

**Files:** `scripts/clover_sandbox.py`, `popcore_app/tests/test_clover_sandbox_probe.py`.

**Interface:** `POST /clover-sandbox/device-snapshot` accepts JSON `{merchantId, deviceId, sequence, order}` and `Authorization: Bearer <configured bridge secret>`. `GET /clover-sandbox/orders` returns current effective orders with per-order source metadata.

- [ ] Add failing tests for credential and device rejection, replay, out-of-order updates, item removal, and cloud catch-up.
- [ ] Run the focused probe tests and confirm those tests fail.
- [ ] Implement a private SQLite device snapshot table and allowlisted validation. Accept only a strictly increasing persisted sequence per paired device; same sequence and content is an idempotent retry. Merge current device item state with cloud-confirmed payments only when the cloud has the same full line-item set and total.
- [ ] Run focused tests and confirm they pass.

### Task 2: Checkout adaptation

**Files:** `popcore_app/blueprints/clover_sandbox.py`, `popcore_app/tests/test_clover_sandbox_adapter.py`.

**Interface:** Existing checkout API consumes per-order `source`, `seen_at`, and `reconciled` fields from the probe while retaining its own permissions and payment confirmation rules.

- [ ] Add failing tests for device scans entering the queue, stale state, cloud payments, and no quantity regression.
- [ ] Run the focused adapter tests and confirm failure.
- [ ] Map device snapshots to existing checkout details, withholding pricing guidance if local money is unavailable. Preserve cloud-only completed status.
- [ ] Run focused tests and confirm pass.

### Task 3: Android companion

**Files:** `android/clover-sync/` Gradle project and `README.md`.

**Interface:** Companion reads `OrderConnector` callbacks and `getOrder(orderId)`, sends the schema from Task 1 over HTTPS, and displays its connection/pairing state.

- [ ] Create a local serializer test for seven distinct Clover line IDs, repeated callbacks, and removal.
- [ ] Run it and confirm failure before implementation.
- [ ] Implement a foreground listener, 100 ms burst coalescing, persisted monotonic sequence, one in-flight HTTP request, and retry of the latest complete snapshot. Configure endpoint and secret at runtime; keep secrets out of the APK.
- [ ] Build a debug APK with the installed Android SDK and inspect its manifest/permissions. Validate on the emulator if the Clover SDK and device access permit it.

### Task 4: Integrated verification

**Files:** `docs/clover-checkout-rehearsal.md`, focused test files.

- [ ] Run all focused Clover tests, the backend suite, and `git diff --check`.
- [ ] Verify exact same-order item IDs and amounts across Android local read, probe, checkout adapter, and browser. Time ten additions and a three-item burst if sandbox installation is available.
- [ ] Record measured median, p95, maximum, and limitations; update the rehearsal guide.
- [ ] Review the full uncommitted diff for correctness and unintended changes.
