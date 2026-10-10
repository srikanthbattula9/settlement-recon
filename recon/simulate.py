"""Synthetic settlement data: a day's captures and refunds plus the
settlement file(s) a gateway would eventually send back, with explicit
control over every kind of mismatch.

Design: the low-level functions (build_settlement_lines, split_into_payouts)
take explicit txn_ref sets, so a test can assert exactly what happens to a
given ref. random_chaos() is the only probabilistic layer, used by the CLI
for a realistic demo run -- never by tests, which stay deterministic.
"""
import argparse
import csv
import io
import os
import random
from dataclasses import dataclass
from datetime import date, timedelta

import psycopg

from recon.ingest import ingest_file
from recon.match import run_match

GATEWAYS = {
    "gw_alpha": dict(fee_bps=200, fee_fixed_minor=10, settlement_sla_days=2),
    "gw_beta":  dict(fee_bps=150, fee_fixed_minor=20, settlement_sla_days=3),
    "gw_gamma": dict(fee_bps=250, fee_fixed_minor=5,  settlement_sla_days=1),
}


def ensure_gateways(conn):
    for gateway_id, cfg in GATEWAYS.items():
        conn.execute(
            "INSERT INTO gateways (gateway_id, fee_bps, fee_fixed_minor, settlement_sla_days) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT (gateway_id) DO NOTHING",
            (gateway_id, cfg["fee_bps"], cfg["fee_fixed_minor"], cfg["settlement_sla_days"]),
        )


@dataclass
class LedgerRow:
    txn_ref: str
    amount_minor: int
    captured_on: date
    entry_type: str = "capture"


def make_captures(rng, gateway_id, day, n, min_minor=500, max_minor=50_000):
    return [LedgerRow(f"{gateway_id}_{day.isoformat()}_{i:04d}", rng.randint(min_minor, max_minor), day)
            for i in range(n)]


def add_refunds(rows, refund_refs, rng, full=True):
    """One refund LedgerRow per ref in refund_refs. The refund gets its own
    txn_ref -- it has to, since txn_ref is ledger_transactions' primary key."""
    by_ref = {r.txn_ref: r for r in rows}
    out = list(rows)
    for ref in refund_refs:
        cap = by_ref[ref]
        amt = -cap.amount_minor if full else -rng.randint(1, cap.amount_minor)
        out.append(LedgerRow(f"{ref}_rf", amt, cap.captured_on, "refund"))
    return out


def write_ledger(conn, gateway_id, rows):
    for r in rows:
        conn.execute(
            "INSERT INTO ledger_transactions (txn_ref, gateway_id, amount_minor, captured_on, entry_type) "
            "VALUES (%s,%s,%s,%s,%s)", (r.txn_ref, gateway_id, r.amount_minor, r.captured_on, r.entry_type))


def build_settlement_lines(rows, fee_bps, fee_fixed_minor, *, settled_on,
                           missing_refs=(), duplicate_refs=(), amount_mismatch_refs=(),
                           fee_mismatch_refs=(), late_refs=(), late_by_days=5, unknown_count=0):
    """One settlement line per ledger row not in missing_refs, with the
    requested chaos applied to each ref. Returns dicts shaped for render_csv."""
    lines = []
    for r in rows:
        if r.txn_ref in missing_refs:
            continue
        amount = r.amount_minor
        fee = (amount * fee_bps) // 10_000 + fee_fixed_minor if r.entry_type == "capture" else 0
        if r.txn_ref in amount_mismatch_refs:
            amount += 777
        if r.txn_ref in fee_mismatch_refs:
            fee += 111
        settled = settled_on + timedelta(days=late_by_days) if r.txn_ref in late_refs else settled_on
        line = dict(txn_ref=r.txn_ref, line_type=r.entry_type, amount_minor=amount, fee_minor=fee,
                   settled_on=settled.isoformat())
        lines.append(line)
        if r.txn_ref in duplicate_refs:
            lines.append(dict(line))
    for _ in range(unknown_count):
        lines.append(dict(txn_ref="", line_type="capture", amount_minor=0, fee_minor=0,
                          settled_on=settled_on.isoformat()))
    return lines


def render_csv(payout_ref, gateway_id, payout_date, lines, net_override=None):
    net = net_override if net_override is not None else sum(l["amount_minor"] - l["fee_minor"] for l in lines)
    out = io.StringIO()
    out.write("row_type,payout_ref,gateway_id,payout_date,net_amount_minor,line_count\n")
    out.write(f"payout,{payout_ref},{gateway_id},{payout_date.isoformat()},{net},{len(lines)}\n")
    out.write("row_type,txn_ref,line_type,amount_minor,fee_minor,settled_on\n")
    for l in lines:
        out.write(f"detail,{l['txn_ref']},{l['line_type']},{l['amount_minor']},{l['fee_minor']},{l['settled_on']}\n")
    return out.getvalue().encode("utf-8")


def split_into_payouts(lines, n=2):
    """Round-robin split, so each batch gets a mix rather than a contiguous
    chunk -- closer to how a gateway's own batching would land in practice."""
    buckets = [[] for _ in range(n)]
    for i, l in enumerate(lines):
        buckets[i % n].append(l)
    return buckets


@dataclass
class ChaosRates:
    missing: float = 0.0
    duplicate: float = 0.0
    amount_mismatch: float = 0.0
    fee_mismatch: float = 0.0
    late: float = 0.0
    refund: float = 0.0
    unknown_lines: int = 0
    split_payout: bool = False
    bad_payout_total: bool = False


def random_chaos(rng, rows, rates: ChaosRates):
    """Assigns each capture ref to at most one mismatch bucket (first match
    wins, in the order below) and independently rolls for a refund."""
    capture_refs = [r.txn_ref for r in rows if r.entry_type == "capture"]
    buckets = dict(missing=set(), duplicate=set(), amount_mismatch=set(),
                  fee_mismatch=set(), late=set(), refund=set())
    cum = [("missing", rates.missing), ("duplicate", rates.duplicate),
           ("amount_mismatch", rates.amount_mismatch), ("fee_mismatch", rates.fee_mismatch),
           ("late", rates.late)]
    for ref in capture_refs:
        roll, acc = rng.random(), 0.0
        for name, rate in cum:
            acc += rate
            if roll < acc:
                buckets[name].add(ref)
                break
        if rng.random() < rates.refund and ref not in buckets["missing"]:
            buckets["refund"].add(ref)
    return buckets


def run_demo_day(conn, gateway_id, day, n, rates: ChaosRates, seed=0):
    """Generate a day's captures and refunds, build the (possibly chaotic)
    settlement file(s), ingest, and match. Returns the match stats. Caller
    commits. Meant for one demo invocation on a fresh day -- run_match has
    no date filter on settlements, so calling this for multiple days against
    the same gateway reprocesses everything ingested so far each time."""
    rng = random.Random(seed)
    cfg = GATEWAYS[gateway_id]
    rows = make_captures(rng, gateway_id, day, n)
    chaos = random_chaos(rng, rows, rates)
    rows = add_refunds(rows, chaos["refund"], rng)
    write_ledger(conn, gateway_id, rows)

    late_by_days = cfg["settlement_sla_days"] + 3
    lines = build_settlement_lines(
        rows, cfg["fee_bps"], cfg["fee_fixed_minor"], settled_on=day,
        missing_refs=chaos["missing"], duplicate_refs=chaos["duplicate"],
        amount_mismatch_refs=chaos["amount_mismatch"], fee_mismatch_refs=chaos["fee_mismatch"],
        late_refs=chaos["late"], late_by_days=late_by_days, unknown_count=rates.unknown_lines,
    )
    batches = split_into_payouts(lines, 2) if rates.split_payout else [lines]
    for i, batch in enumerate(batches):
        if not batch:
            continue
        net_override = 999_999_999 if (rates.bad_payout_total and i == 0) else None
        raw = render_csv(f"po_{gateway_id}_{day.isoformat()}_{i}", gateway_id, day, batch, net_override)
        ingest_file(conn, gateway_id, f"{gateway_id}_{day.isoformat()}_{i}.csv", raw)

    run_date = day + timedelta(days=late_by_days + 1)
    return run_match(conn, gateway_id, run_date)


def main():
    ap = argparse.ArgumentParser(description="Generate and reconcile one synthetic settlement day.")
    ap.add_argument("--gateway", default="gw_alpha", choices=list(GATEWAYS))
    ap.add_argument("--day", default=date.today().isoformat())
    ap.add_argument("-n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--chaos", choices=["none", "light", "heavy"], default="light")
    args = ap.parse_args()

    presets = {
        "none": ChaosRates(),
        "light": ChaosRates(missing=0.05, duplicate=0.03, amount_mismatch=0.03, fee_mismatch=0.03,
                            late=0.05, refund=0.08, unknown_lines=1),
        "heavy": ChaosRates(missing=0.10, duplicate=0.08, amount_mismatch=0.08, fee_mismatch=0.08,
                            late=0.10, refund=0.15, unknown_lines=3, split_payout=True,
                            bad_payout_total=True),
    }
    rates = presets[args.chaos]
    day = date.fromisoformat(args.day)
    dsn = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5434/recon")
    with psycopg.connect(dsn) as conn:
        ensure_gateways(conn)
        conn.commit()
        stats = run_demo_day(conn, args.gateway, day, args.n, rates, seed=args.seed)
        conn.commit()
        print(f"gateway={args.gateway} day={day} n={args.n} chaos={args.chaos} seed={args.seed}")
        print(f"matched={stats.matched} exceptions={stats.exceptions} by_class={stats.by_class}")


if __name__ == "__main__":
    main()
