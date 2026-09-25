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
