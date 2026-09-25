-- settlement-recon: schema

CREATE TABLE gateways (
    gateway_id      TEXT PRIMARY KEY,           -- e.g. 'gw_alpha', 'gw_beta', 'gw_gamma'
    fee_bps         INT NOT NULL,               -- contracted fee in basis points
    fee_fixed_minor INT NOT NULL DEFAULT 0,     -- fixed per-txn fee, cents
    settlement_sla_days INT NOT NULL DEFAULT 2
);

-- What our books say happened.
CREATE TABLE ledger_transactions (
    txn_ref         TEXT PRIMARY KEY,
    gateway_id      TEXT NOT NULL REFERENCES gateways(gateway_id),
    amount_minor    BIGINT NOT NULL,
    currency        CHAR(3) NOT NULL DEFAULT 'USD',
    captured_on     DATE NOT NULL
);
CREATE INDEX ON ledger_transactions(gateway_id, captured_on);

-- What the gateway says it settled. One row per line in a settlement file.
CREATE TABLE settlements (
    settlement_id   BIGSERIAL PRIMARY KEY,
    gateway_id      TEXT NOT NULL REFERENCES gateways(gateway_id),
    file_name       TEXT NOT NULL,
    txn_ref         TEXT NOT NULL,
    settled_amount_minor BIGINT NOT NULL,
    settled_fee_minor    BIGINT NOT NULL,
    settled_on      DATE NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON settlements(gateway_id, txn_ref);

-- Every mismatch the engine finds, with the evidence to explain it.
CREATE TABLE exceptions (
    exception_id    BIGSERIAL PRIMARY KEY,
    run_date        DATE NOT NULL,
    gateway_id      TEXT NOT NULL REFERENCES gateways(gateway_id),
    txn_ref         TEXT,
    class           TEXT NOT NULL CHECK (class IN (
                        'missing_in_settlement','missing_in_ledger','amount_mismatch',
                        'fee_mismatch','duplicate_settlement','timing_lag')),
    expected        JSONB,
    observed        JSONB,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','ignored')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON exceptions(run_date, gateway_id, class);
