# Abandoned checkout refunds

Goal: finish the approved backend refund-and-cancel workflow without erasing received money, creating a fake sale or moving inventory. Reuse checkout authorization/version/idempotency and existing closing/recovery machinery. No frontend redesign or provider refund calls.

A scoped manager marks a paid, unfinalized checkout abandoned with a reason, cancelling pending attempts and freezing collection/finalization. They record actual refunds against each completed attempt, in the original tender, with positive cents, actual refund business date, unique store refund reference, reason and explicit confirmation that both receipt and refund were checked. Refunds may be partial; the last full repayment cancels the order. Cash/electronic receipts retain the original checkout business date, refunds use their own date. Manager-confirmed receipts and dated refunds feed closing and tender reports without sale duplication. Refund evidence remains on the original attempt and survives cancellation/backup.

1. Add failing API tests for abandon/refund, roles, over-refund, dates, retries and cancellation.
2. Add append-only refund records and workflow transitions. Verify money/stock invariants and concurrency.
3. Add closing/tender facts and source tokens on all affected dates, including late adjustments. Verify same-day and cross-day cash, electronic separation, blocked closing until settlement and backup/restore.
4. Review and run backend suite, startup/migration and recovery checks. Leave frontend files and production unchanged.

Completed: all four steps verified. 432 backend tests (28 checkout), startup/migration replay, complete backup recovery and independent final review passed. Frontend controls and live provider verification remain deferred.
