from datetime import date
from recon.classify import (Ledger, Settlement, amount_mismatch, fee_mismatch,
                            timing_lag, duplicate_settlement, expected_fee)

L = Ledger("t1", "gw_alpha", 10_000, date(2026, 9, 20))


def test_expected_fee_bps_plus_fixed():
    assert expected_fee(10_000, 250, 30) == 280  # 2.5% + $0.30


def test_amount_mismatch_detected():
    s = Settlement("t1", "gw_alpha", 9_990, 280, date(2026, 9, 22))
    assert amount_mismatch(L, s) is True


def test_amount_within_tolerance_is_not_mismatch():
    s = Settlement("t1", "gw_alpha", 9_999, 280, date(2026, 9, 22))
    assert amount_mismatch(L, s, tolerance_minor=1) is False


def test_fee_mismatch_detected():
    s = Settlement("t1", "gw_alpha", 10_000, 300, date(2026, 9, 22))
    assert fee_mismatch(L, s, fee_bps=250, fee_fixed_minor=30) is True


def test_timing_lag_beyond_sla():
    s = Settlement("t1", "gw_alpha", 10_000, 280, date(2026, 9, 25))
    assert timing_lag(L, s, sla_days=2) is True


def test_duplicate_settlement_found():
    a = Settlement("t1", "gw_alpha", 10_000, 280, date(2026, 9, 22))
    b = Settlement("t1", "gw_alpha", 10_000, 280, date(2026, 9, 23))
    assert duplicate_settlement([a, b]) == {"t1"}
