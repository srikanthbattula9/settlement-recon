# settlement-recon

A **settlement reconciliation engine**: ingests daily settlement files from multiple payment gateways, matches each settled transaction against an internal double-entry ledger, and classifies every mismatch with a tested rule per class.

This is the post-transaction problem: after money moves, does what the gateway says it paid match what our books say we are owed? At redBus I ran this across seven gateways by hand-built scripts and found hundreds of mismatches a month. This is that job, rebuilt properly.

> Simulated gateways and synthetic data. No real settlements, no real money.

## What it does

```
gateway settlement files (CSV, one per gateway, daily)
            │
            ▼
      [ingest + normalize]  ──▶  settlements table (gateway, txn_ref, amount, fee, settled_on)
            │
            ▼
      [match against ledger]  ──▶  matched / unmatched
            │
            ▼
      [classify mismatches]   ──▶  exceptions table (class, expected, observed, evidence)
            │
            ▼
      FastAPI /exceptions  +  Grafana panel (mismatch rate by gateway and class)
```

## Mismatch classes

| Class | Meaning | Detection rule |
|---|---|---|
| `missing_in_settlement` | Ledger has a captured txn the gateway never settled | captured > N days ago, no settlement row |
| `missing_in_ledger` | Gateway settled something we have no record of | settlement row, no ledger txn_ref |
| `amount_mismatch` | Same txn, different amount | abs(ledger − settled) > tolerance |
| `fee_mismatch` | Fee differs from contracted rate | settled_fee ≠ expected_fee(amount, gateway) |
| `duplicate_settlement` | Gateway settled the same txn twice | same txn_ref appears twice across files |
| `payout_total_mismatch` | A payout's lines don't sum to its stated net | `sum(line.amount - line.fee) != payout.net_amount_minor` |
| `unknown_line` | A settlement row the matcher can't even attempt to place | blank `txn_ref`, or a `line_type` outside the recognized set -- the suspense queue |
| `timing_lag` | Settled, but later than SLA | settled_on − captured_on > SLA days |

Each class has its own test with a fixture that triggers exactly that case and no other.

## Status

| Piece | State |
|---|---|
| Ledger + settlements + payouts schema | done |
| Ingest (simulated CSV, idempotent by file content hash) | done, tested |
| Matcher (ledger vs. settlements, per-gateway, per-run-date) | done, tested |
| Eight mismatch classifiers + tests | done, tested (6 original + payout_total_mismatch + unknown_line) |
| Gateway simulators (3 gateways, configurable fee schedules and chaos) | planned |
| `/exceptions` API | planned |
| Grafana dashboard | planned |


## Ingest and payout decomposition

A gateway doesn't settle one transaction at a time; it pays out a batch:
`net = captures - refunds - fees +/- adjustments`, and the batch can be
split across multiple files for one day, or negative (refunds exceeded
captures for the period -- a real case, not an error).

`recon/ingest.py` parses one simulated settlement CSV per call: a payout
summary row, then detail rows typed `capture`/`refund`/`fee`/`adjustment`/
`chargeback`. Ingestion is idempotent by the sha256 of the file's bytes --
re-ingesting the identical file is a no-op, and a file with the same name
but different content is rejected rather than silently overwritten.

`recon/match.py` reconciles a gateway's ledger against everything ingested
for it as of a run date. Only `capture`/`refund`/`chargeback` lines are
matched to `ledger_transactions`, by `(txn_ref, line_type)` -- `fee` and
`adjustment` lines have no ledger counterpart and only feed the payout-total
check. Ledger amounts are signed (capture positive, refund/chargeback
negative) so a matched pair compares directly with no sign-flipping.
`ledger_transactions.txn_ref` is a primary key, so a capture and its refund
are guaranteed distinct rows at the schema level, not by matcher logic.

Tested (`tests/test_ingest.py`, `tests/test_match.py`, 19 tests): clean
ingest, identical re-ingest is a no-op, same-name-different-content
rejected, split payout across two files, negative-net payout, wrong
gateway_id rejected; clean match with no exceptions; each of the eight
exception classes triggered in isolation; a duplicated settlement line
flagged exactly once, not once per copy; fee/adjustment lines never
produce `missing_in_ledger`; `missing_in_settlement` only fires once the
SLA window has passed; the capture/refund primary-key guarantee.

`run_match()` is not idempotent across repeated calls for the same
(gateway, date) -- re-running writes duplicate exception rows. It's meant
to be run once per day per gateway; nothing enforces that yet.

## Stack

Python 3.12 · FastAPI · PostgreSQL · pytest · GitHub Actions · Grafana

## Running

```bash
docker compose up -d
make migrate
make simulate DAY=2026-09-25     # generates 3 gateway files with injected mismatches
make reconcile DAY=2026-09-25
make test
```
