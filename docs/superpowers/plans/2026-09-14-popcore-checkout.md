# POPCORE checkout implementation

Goal: complete the POPCORE cashier and assigned-phone workflow while Clover access is blocked.
Architecture: separate unpaid checkouts with immutable product/money snapshots and payment attempts; reuse sale posting, inventory ledger, private evidence storage and closing. Stack: existing Flask/SQLite/React/Ant Design. Spec: the user's approved cashier → assigned phone → payment → sale → stock → closing flow.

Constraints: no Today catalog or Schedule changes, no production deployment, no fake authentication in application code, no Clover payment processing or verification claims. Staff manually record money already received. A photo never confirms payment. Clover remains explicitly disconnected; future verified intake must map provider identities to the same workflow. Orders do not reserve inventory. Amount changes require cancelling an unpaid checkout and creating another; partial collections must be completed and resolved through existing sale/refund records.

1. Add isolated API checks for split/replaced attempts, private evidence, permissions, duplicate requests, finalization and closing blockers. Verify they fail without the feature.
2. Implement checkout storage and scoped routes. Finalize into one sale using existing transactional posting; preserve completed payments if finalization fails. Include staged images in backups. Verify API tests and existing sale/closing/recovery checks.
3. Add native pending checkout creation, queue and phone detail using the established application components. Preserve stable requests during ambiguous errors. Verify TypeScript, isolated browser flow and mobile layout.
4. Review changes and record limitations and actual validation. No commit, push or deployment in this task.
