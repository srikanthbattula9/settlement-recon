-- A capture and its refund are different ledger rows with different
-- txn_refs (the simulator always mints a fresh ref per entry), but the
-- matcher needs to know which kind each one is to apply the right rule
-- (refund amounts are negative on the settlement side, fees never appear
-- here at all). Chargebacks are rare but real enough to model explicitly
-- rather than folding into refund.
ALTER TABLE ledger_transactions
    ADD COLUMN entry_type TEXT NOT NULL DEFAULT 'capture'
        CHECK (entry_type IN ('capture', 'refund', 'chargeback'));
ALTER TABLE ledger_transactions ALTER COLUMN entry_type DROP DEFAULT;
