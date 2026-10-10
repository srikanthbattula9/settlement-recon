"""Matches settlement lines against the ledger and writes exception rows.

Conventions this module establishes (nothing pinned these down before):
  - ledger_transactions.amount_minor is SIGNED: positive for a capture,
    negative for a refund or chargeback. settlements.settled_amount_minor
    follows the same sign, so a matched pair compares directly.
  - a settlement line's contribution to its payout is
    settled_amount_minor - settled_fee_minor. A payout's net_amount_minor
    must equal the sum of that across EVERY line in it, including fee and
    adjustment lines (which carry their own signed amount_minor and a
    settled_fee_minor of 0).
  - only capture/refund/chargeback lines are matched to ledger_transactions,
    by (txn_ref, line_type == entry_type). fee/adjustment lines never have a
    ledger counterpart; they only enter the payout-total check.

run_match() is not idempotent across runs: calling it twice for the same
(gateway_id, run_date) writes exceptions twice. Callers run it once per day
per gateway, same as the card processor's sweeper is run once per interval.
"""
import json
from dataclasses import dataclass, field

from recon.classify import (
    Ledger, Settlement, amount_mismatch, fee_mismatch, timing_lag,
    duplicate_settlement, payout_total_mismatch, unknown_line, expected_fee,
)

MATCHABLE_TYPES = {"capture", "refund", "chargeback"}


@dataclass
class MatchStats:
    matched: int = 0
    exceptions: int = 0
    by_class: dict = field(default_factory=dict)

    def _bump(self, cls: str) -> None:
        self.exceptions += 1
        self.by_class[cls] = self.by_class.get(cls, 0) + 1


def _insert(conn, run_date, gateway_id, txn_ref, cls, expected, observed):
    conn.execute(
        "INSERT INTO exceptions (run_date, gateway_id, txn_ref, class, expected, observed) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (run_date, gateway_id, txn_ref, cls,
         json.dumps(expected, default=str), json.dumps(observed, default=str)),
    )


def run_match(conn, gateway_id: str, run_date, tolerance_minor: int = 0) -> MatchStats:
    gw = conn.execute(
        "SELECT fee_bps, fee_fixed_minor, settlement_sla_days FROM gateways WHERE gateway_id = %s",
        (gateway_id,),
    ).fetchone()
    if gw is None:
        raise ValueError(f"unknown gateway_id {gateway_id!r}")
    fee_bps, fee_fixed_minor, sla_days = gw

    ledger_by_key = {
        (ref, etype): Ledger(ref, gateway_id, amt, captured)
        for ref, amt, captured, etype in conn.execute(
            "SELECT txn_ref, amount_minor, captured_on, entry_type FROM ledger_transactions "
            "WHERE gateway_id = %s", (gateway_id,)).fetchall()
    }
    settlement_rows = conn.execute(
        "SELECT txn_ref, settled_amount_minor, settled_fee_minor, settled_on, payout_ref, line_type "
        "FROM settlements WHERE gateway_id = %s ORDER BY settlement_id", (gateway_id,),
    ).fetchall()

    stats = MatchStats()
    matched_keys = set()
    dupes = duplicate_settlement([
        Settlement(ref, gateway_id, amt, fee, settled)
        for ref, amt, fee, settled, payout_ref, ltype in settlement_rows
        if ltype in MATCHABLE_TYPES
    ])
    dupes_reported = set()

    for ref, amt, fee, settled, payout_ref, ltype in settlement_rows:
        if unknown_line(ref, ltype):
            stats._bump("unknown_line")
            _insert(conn, run_date, gateway_id, ref, "unknown_line", {"line_type": ltype}, {"txn_ref": ref})
            continue
        if ltype not in MATCHABLE_TYPES:
            continue  # fee / adjustment: no ledger counterpart; covered by the payout-total check

        if ref in dupes and ref not in dupes_reported:
            dupes_reported.add(ref)
            stats._bump("duplicate_settlement")
            _insert(conn, run_date, gateway_id, ref, "duplicate_settlement", {}, {"txn_ref": ref})

        key = (ref, ltype)
        ledger_row = ledger_by_key.get(key)
        if ledger_row is None:
            stats._bump("missing_in_ledger")
            _insert(conn, run_date, gateway_id, ref, "missing_in_ledger",
                   {}, {"settled_amount_minor": amt, "line_type": ltype})
            continue
        matched_keys.add(key)
        settlement = Settlement(ref, gateway_id, amt, fee, settled)

        if amount_mismatch(ledger_row, settlement, tolerance_minor):
            stats._bump("amount_mismatch")
            _insert(conn, run_date, gateway_id, ref, "amount_mismatch",
                   {"amount_minor": ledger_row.amount_minor}, {"settled_amount_minor": amt})
            continue  # amount's already wrong; don't also flag fee/timing on top of it

        if ltype == "capture" and fee_mismatch(ledger_row, settlement, fee_bps, fee_fixed_minor):
            stats._bump("fee_mismatch")
            _insert(conn, run_date, gateway_id, ref, "fee_mismatch",
                   {"expected_fee_minor": expected_fee(ledger_row.amount_minor, fee_bps, fee_fixed_minor)},
                   {"settled_fee_minor": fee})

        if timing_lag(ledger_row, settlement, sla_days):
            stats._bump("timing_lag")
            _insert(conn, run_date, gateway_id, ref, "timing_lag",
                   {"sla_days": sla_days}, {"days_late": (settled - ledger_row.captured_on).days})

        stats.matched += 1

    for (ref, etype), ledger_row in ledger_by_key.items():
        if (ref, etype) in matched_keys:
            continue
        if (run_date - ledger_row.captured_on).days <= sla_days:
            continue  # still inside the SLA window; not late yet
        stats._bump("missing_in_settlement")
        _insert(conn, run_date, gateway_id, ref, "missing_in_settlement",
               {"amount_minor": ledger_row.amount_minor, "entry_type": etype}, {})

    payout_totals = {}
    for ref, amt, fee, settled, payout_ref, ltype in settlement_rows:
        if payout_ref is not None:
            payout_totals[payout_ref] = payout_totals.get(payout_ref, 0) + (amt - fee)
    for payout_ref, stated_net in conn.execute(
        "SELECT payout_ref, net_amount_minor FROM payouts WHERE gateway_id = %s", (gateway_id,)
    ).fetchall():
        line_total = payout_totals.get(payout_ref, 0)
        if payout_total_mismatch(stated_net, line_total):
            stats._bump("payout_total_mismatch")
            _insert(conn, run_date, gateway_id, None, "payout_total_mismatch",
                   {"stated_net_minor": stated_net}, {"line_total_minor": line_total})

    return stats
