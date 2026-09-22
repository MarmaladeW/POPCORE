# Checkout Tender Pricing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the approved tender-dependent checkout pricing before the first split payment.

**Architecture:** Keep integer-cent policy in `checkoutPricing.ts` and make the existing checkout screen consume that policy. Clover-confirmed totals remain authoritative; this work only changes the target estimate and pre-payment instructions.

**Tech Stack:** React, TypeScript, Node test runner, existing Playwright browser check.

**Spec:** `docs/superpowers/specs/2026-09-22-checkout-tender-pricing-design.md`

## Global Constraints

- Preserve the five actual tenders: Card, Cash, E-transfer, WeChat Pay, and Alipay.
- Do not add dependencies, backend mutations, Clover writes, production static assets, commits, pushes, or deployments.
- Never record a suggested amount until the Clover-confirmed collected total matches it.
- Make the smallest change in the existing pricing helper and checkout screen.

## Review Focus

- CA$9.99 Card-inclusive split remains CA$9.99.
- CA$10.00 Card-inclusive split remains CA$10.00.
- CA$10.99 Card-inclusive split becomes CA$10.00.
- Card-only payment keeps the exact total.
- Non-Card supported tenders retain the existing cash-discount suggestion.

---

### Task 1: Tender-dependent pricing policy

**Files:**
- Modify: `popcore_app/frontend/src/lib/checkoutPricing.ts`
- Test: `popcore_app/frontend/src/lib/checkoutPricing.test.ts`

**Interfaces:**
- Consumes: existing `suggestPayable(totalCents: number): number`
- Produces: `suggestPaymentTarget(totalCents: number, tender: string, mixedWithCard: boolean): number`

- [x] **Step 1: Write failing table-driven tests**

Add literal expectations for Card-only exact totals, each discount-eligible tender, the approved `2831 -> 2800` and `3191 -> 3100` examples, and the `999`, `1000`, and `1099` boundary.

- [x] **Step 2: Run the focused test and verify RED**

Run: `node --test src/lib/checkoutPricing.test.ts`

Expected: FAIL because `suggestPaymentTarget` is not exported.

- [x] **Step 3: Implement the minimum policy helper**

Validate integer cents through the existing path. Return the original total for Card-only, the existing suggestion for non-Card-only, and either the exact sub-CA$10 total or whole-dollar floor for Card-inclusive splits.

- [x] **Step 4: Run the focused test and verify GREEN**

Run: `node --test src/lib/checkoutPricing.test.ts`

Expected: all checkout pricing tests pass.

### Task 2: Pre-payment Card-inclusive split control

**Files:**
- Modify: `popcore_app/frontend/src/pages/Sales/Checkout.tsx`
- Modify: `popcore_app/tests/browser/check_checkout_focus.py`
- Modify: `docs/popcore-checkout.md`

**Interfaces:**
- Consumes: `suggestPaymentTarget(totalCents, tender, mixedWithCard)` from Task 1
- Produces: a “This split includes Card” choice before the first payment and tender-specific checkout instructions

- [x] **Step 1: Add the failing browser contract**

Use an unpaid CA$41.34 fixture. Verify Card-only shows CA$41.34, selecting Split payment with Card shows CA$41.00, switching the first tender to Cash preserves CA$41.00 while the Card-inclusive choice remains selected, and clearing that choice restores the cash-discount target of CA$40.00.

- [x] **Step 2: Run the browser check and verify RED**

Run: `python3 popcore_app/tests/browser/check_checkout_focus.py`

Expected: FAIL because the Card-inclusive split choice and target behavior do not exist.

- [x] **Step 3: Implement the minimum checkout state and copy**

Add one boolean to the existing per-order draft. Recalculate only an unconfirmed, unpaid target. Show the Card-inclusive choice only for split payments, label its adjustment as rounding rather than a cash discount, and name the actual selected custom tender in instructions.

- [x] **Step 4: Document the approved matrix**

Update `docs/popcore-checkout.md` with the five tenders, mixed-Card whole-dollar floor, and sub-CA$10 exception. Keep live/provider behavior explicitly unverified.

- [x] **Step 5: Run affected and project verification**

Run:

```bash
node --test src/lib/checkoutPricing.test.ts
npm test
npm run build -- --outDir ../../.local/frontend-build
python3 popcore_app/tests/browser/check_checkout_focus.py
git diff --check
```

Expected: all commands exit 0; committed production static assets remain unchanged.
