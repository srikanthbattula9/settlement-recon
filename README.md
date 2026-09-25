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
| `timing_lag` | Settled, but later than SLA | settled_on − captured_on > SLA days |

Each class has its own test with a fixture that triggers exactly that case and no other.

## Status

| Piece | State |
|---|---|
| Ledger + settlements schema | in progress |
| Gateway simulators (3 gateways, configurable fee schedules and chaos) | planned |
| Ingest + normalize | planned |
| Matcher | planned |
| Six mismatch classifiers + tests | planned |
| `/exceptions` API | planned |
| Grafana dashboard | planned |

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
