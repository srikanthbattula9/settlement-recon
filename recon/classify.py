"""Mismatch classification rules. Pure functions; one per class; each has its own test."""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Ledger:
    txn_ref: str
    gateway_id: str
    amount_minor: int
    captured_on: date


@dataclass(frozen=True)
class Settlement:
    txn_ref: str
    gateway_id: str
    settled_amount_minor: int
    settled_fee_minor: int
    settled_on: date


def expected_fee(amount_minor: int, fee_bps: int, fee_fixed_minor: int) -> int:
    return (amount_minor * fee_bps) // 10_000 + fee_fixed_minor


def amount_mismatch(l: Ledger, s: Settlement, tolerance_minor: int = 0) -> bool:
    return abs(l.amount_minor - s.settled_amount_minor) > tolerance_minor


def fee_mismatch(l: Ledger, s: Settlement, fee_bps: int, fee_fixed_minor: int) -> bool:
    return s.settled_fee_minor != expected_fee(l.amount_minor, fee_bps, fee_fixed_minor)


def timing_lag(l: Ledger, s: Settlement, sla_days: int) -> bool:
    return (s.settled_on - l.captured_on).days > sla_days


def duplicate_settlement(settlements: list[Settlement]) -> set[str]:
    seen, dupes = set(), set()
    for s in settlements:
        if s.txn_ref in seen:
            dupes.add(s.txn_ref)
        seen.add(s.txn_ref)
    return dupes


def payout_total_mismatch(stated_net_minor: int, line_totals_minor: int) -> bool:
    """line_totals_minor is the sum of every detail line's signed amount
    (captures positive, refunds/fees/chargebacks as they appear, typically
    negative). A mismatch means the batch's lines don't add up to what the
    gateway claims it paid out -- the payout itself is suspect, independent
    of whether any individual line matches the ledger."""
    return stated_net_minor != line_totals_minor


def unknown_line(txn_ref: str | None, line_type: str) -> bool:
    """True for a settlement row the matcher can't even attempt to place:
    no txn_ref at all, or a line_type outside the ones we recognize. This is
    the suspense bucket -- a data problem with the file, not a reconciliation
    gap. A well-formed row that simply has no matching ledger row is
    missing_in_ledger instead, not this."""
    known_types = {"capture", "refund", "fee", "adjustment", "chargeback"}
    return not txn_ref or not txn_ref.strip() or line_type not in known_types


def payout_total_mismatch(stated_net_minor: int, line_totals_minor: int) -> bool:
    """line_totals_minor is the sum of every detail line's signed amount
    (captures positive, refunds/fees/chargebacks as they appear, typically
    negative). A mismatch means the batch's lines don't add up to what the
    gateway claims it paid out -- the payout itself is suspect, independent
    of whether any individual line matches the ledger."""
    return stated_net_minor != line_totals_minor


def unknown_line(txn_ref: str | None, line_type: str) -> bool:
    """True for a settlement row the matcher can't even attempt to place:
    no txn_ref at all, or a line_type outside the ones we recognize. This is
    the suspense bucket -- a data problem with the file, not a reconciliation
    gap. A well-formed row that simply has no matching ledger row is
    missing_in_ledger instead, not this."""
    known_types = {"capture", "refund", "fee", "adjustment", "chargeback"}
    return not txn_ref or not txn_ref.strip() or line_type not in known_types
