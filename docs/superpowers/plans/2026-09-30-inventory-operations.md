# Inventory operations implementation plan

Goal: complete common series-level store tasks without repeated product selection, while preserving exact design identities and the existing inventory ledger.

The user authorized improvements based on the previous inventory gap review. This extends existing flows in the current isolated worktree. No new stock ledger, dependencies, activation, production writes, commit, push, or deployment.

- [x] Whole-series receiving/count worksheet: reviewed location, explicit quantities for confirmed designs, blank omitted and count zero retained; review saved draft before posting. Verify multiline payloads, role/scope changes, and unchanged uncertain retries at phone/desktop sizes.
- [x] Guided product stock actions: same-store saleable moves and opening a sealed set using its explicit reviewed conversion. Preserve opened-set stock and native units; verify versions, quantity bounds, locked pending drafts and retry.
- [x] Inventory attention filters: unknown, out of stock, floor replenishment, and hold/damaged stock. Never treat unknown as zero or replenish one store from another without an explicit transfer. Test mixed scopes and dispositions.
- [x] Exact-product reference photos using the existing protected image storage and authenticated reads. Do not invent demo merchandise photos or infer identity from a picture.
- [x] Searchable/paginated movement history and authorized document detail with actor, reason, source and correction references. Validate filters and scope server-side; ensure older entries load without duplicates.
- [x] Fix count-create replay so captured stock changes do not change explicit request intent; preserve legacy retries, current access checks, and mismatch rejection.
- [x] Review, focused backend and frontend tests, responsive browser checks, isolated production-mode build, updated local demo.

Verification focus: partial counts must not zero omitted designs; ambiguous requests must freeze until reconciled; retained opened sets cannot be silently moved as loose boxes; all-store views remain read-only; late responses cannot cross the current store/user scope.

Reference: Square's Manage stock worksheet spans variants at one location with keyboard navigation (https://squareup.com/help/us/en/article/8331-set-up-inventory-tracking). Shopify's inventory history exposes actor, date, activity, location/state changes and reasons (https://help.shopify.com/en/manual/products/inventory/adjusting-inventory/adjustment-history). These are workflow references; POPCORE retains its reviewed-count and immutable-ledger rules.

Verified locally: 587 backend tests; 37 frontend tests; TypeScript and isolated production-mode build; series worksheet, stock actions, history, attention/photo, existing design inventory, and real Flask identification browser checks. Mobile widths390/768 and desktop1440 were covered across the browser checks. Lost-response tests inspect the same request key and persisted stock, and use disposable SQLite plus test authentication. Existing local demo sample data was preserved during backend refresh. Production Auth0, opening counts, external integrations and live deployment remain unverified.
