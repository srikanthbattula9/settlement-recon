-- Payout decomposition: a gateway settles in payout batches, not one
-- transaction at a time. net_amount = captures - refunds - fees +/- adjustments.
-- A negative net_amount is a valid, real case (refunds exceeded captures for
-- the period) and is not an error.

CREATE TABLE payouts (
    payout_ref      TEXT PRIMARY KEY,           -- gateway's own reference for the batch
    gateway_id      TEXT NOT NULL REFERENCES gateways(gateway_id),
    payout_date     DATE NOT NULL,
    net_amount_minor BIGINT NOT NULL,           -- as stated by the gateway; can be negative
    line_count      INT NOT NULL DEFAULT 0,     -- detail lines claimed; checked against actual count
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON payouts(gateway_id, payout_date);

-- Every settlement file we've ingested, by content hash, so re-ingesting the
-- same file is a no-op and a same-named file with different bytes is an error
-- rather than a silent overwrite.
CREATE TABLE ingested_files (
    file_name       TEXT NOT NULL,
    gateway_id      TEXT NOT NULL REFERENCES gateways(gateway_id),
    sha256          TEXT NOT NULL,
    row_count       INT NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gateway_id, file_name)
);

-- Settlement lines now belong to a payout and carry a type. A detail row with
-- line_type='payout' doesn't exist; that's the payouts table. Detail lines are
-- capture/refund/fee/adjustment/chargeback.
ALTER TABLE settlements
    ADD COLUMN payout_ref TEXT REFERENCES payouts(payout_ref),
    ADD COLUMN line_type  TEXT NOT NULL DEFAULT 'capture'
        CHECK (line_type IN ('capture','refund','fee','adjustment','chargeback'));
CREATE INDEX ON settlements(payout_ref);

-- Two more exception classes: payout totals that don't reconcile to their
-- lines, and settlement lines we can't attribute to any transaction (the
-- suspense queue).
ALTER TABLE exceptions DROP CONSTRAINT exceptions_class_check;
ALTER TABLE exceptions ADD CONSTRAINT exceptions_class_check CHECK (class IN (
    'missing_in_settlement','missing_in_ledger','amount_mismatch',
    'fee_mismatch','duplicate_settlement','timing_lag',
    'payout_total_mismatch','unknown_line'));
