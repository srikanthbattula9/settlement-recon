"""The matcher: one test per exception class, plus a clean run and the
capture/refund non-collision guarantee."""
from datetime import date, timedelta

import psycopg
import pytest

from recon.match import run_match
from tests.conftest import seed_gateway, seed_ledger, seed_payout, seed_settlement

D0 = date(2026, 10, 1)


def _exceptions(conn, gateway_id, cls=None):
    q = "SELECT txn_ref, class, expected, observed FROM exceptions WHERE gateway_id = %s"
    params = [gateway_id]
    if cls:
        q += " AND class = %s"
        params.append(cls)
    return conn.execute(q, params).fetchall()


def test_clean_match_produces_no_exceptions(conn):
    gw = seed_gateway(conn, fee_bps=200, fee_fixed_minor=10, sla_days=2)
    seed_ledger(conn, "t1", gw, 5000, D0, "capture")
    seed_payout(conn, "po1", gw, D0, 5000 - 110)
    seed_settlement(conn, gw, "t1", 5000, 110, D0, payout_ref="po1", line_type="capture")
    stats = run_match(conn, gw, D0 + timedelta(days=1))
    assert stats.exceptions == 0
    assert stats.matched == 1
    assert _exceptions(conn, gw) == []


def test_amount_mismatch_detected(conn):
    gw = seed_gateway(conn)
    seed_ledger(conn, "t1", gw, 5000, D0, "capture")
    seed_settlement(conn, gw, "t1", 4800, 110, D0, line_type="capture")
    stats = run_match(conn, gw, D0)
    assert stats.by_class == {"amount_mismatch": 1}
    (txn_ref, cls, expected, observed), = _exceptions(conn, gw, "amount_mismatch")
    assert txn_ref == "t1" and expected["amount_minor"] == 5000


def test_fee_mismatch_detected(conn):
    gw = seed_gateway(conn, fee_bps=200, fee_fixed_minor=10)
    seed_ledger(conn, "t1", gw, 5000, D0, "capture")
    seed_settlement(conn, gw, "t1", 5000, 999, D0, line_type="capture")   # expected fee = 110
    stats = run_match(conn, gw, D0)
    assert stats.by_class == {"fee_mismatch": 1}


def test_timing_lag_detected(conn):
    gw = seed_gateway(conn, sla_days=2)
    seed_ledger(conn, "t1", gw, 5000, D0, "capture")
    seed_settlement(conn, gw, "t1", 5000, 110, D0 + timedelta(days=5), line_type="capture")
    stats = run_match(conn, gw, D0 + timedelta(days=5))
    assert stats.by_class == {"timing_lag": 1}


def test_missing_in_settlement_only_after_sla(conn):
    gw = seed_gateway(conn, sla_days=2)
    seed_ledger(conn, "t1", gw, 5000, D0, "capture")
    stats_early = run_match(conn, gw, D0 + timedelta(days=1))   # still inside SLA
    assert stats_early.exceptions == 0
    stats_late = run_match(conn, gw, D0 + timedelta(days=5))    # past SLA, still nothing settled
    assert stats_late.by_class == {"missing_in_settlement": 1}


def test_missing_in_ledger(conn):
    gw = seed_gateway(conn)
    seed_settlement(conn, gw, "ghost_txn", 5000, 110, D0, line_type="capture")
    stats = run_match(conn, gw, D0)
    assert stats.by_class == {"missing_in_ledger": 1}


def test_duplicate_settlement_flagged_exactly_once(conn):
    gw = seed_gateway(conn)
    seed_ledger(conn, "t1", gw, 5000, D0, "capture")
    seed_settlement(conn, gw, "t1", 5000, 110, D0, line_type="capture", file_name="a.csv")
    seed_settlement(conn, gw, "t1", 5000, 110, D0, line_type="capture", file_name="b.csv")
    stats = run_match(conn, gw, D0)
    assert stats.by_class == {"duplicate_settlement": 1}        # not 2


def test_unknown_line_blank_txn_ref_goes_to_suspense(conn):
    gw = seed_gateway(conn)
    seed_settlement(conn, gw, "", 1000, 0, D0, line_type="capture")
    stats = run_match(conn, gw, D0)
    assert stats.by_class == {"unknown_line": 1}


def test_fee_and_adjustment_lines_are_not_matched_to_ledger(conn):
    """These line_types have no ledger counterpart by design -- an unmatched
    txn_ref here must not raise missing_in_ledger."""
    gw = seed_gateway(conn)
    seed_settlement(conn, gw, "fee_ref_1", -50, 0, D0, line_type="fee")
    seed_settlement(conn, gw, "adj_ref_1", -25, 0, D0, line_type="adjustment")
    stats = run_match(conn, gw, D0)
    assert stats.exceptions == 0


def test_payout_total_mismatch(conn):
    gw = seed_gateway(conn)
    seed_ledger(conn, "t1", gw, 5000, D0, "capture")
    seed_payout(conn, "po1", gw, D0, 999999)                     # wrong on purpose
    seed_settlement(conn, gw, "t1", 5000, 110, D0, payout_ref="po1", line_type="capture")
    stats = run_match(conn, gw, D0)
    assert "payout_total_mismatch" in stats.by_class


def test_negative_net_payout_with_correct_total_is_clean(conn):
    """Refunds exceeding captures: net is negative, and that alone is not
    an exception as long as the lines sum to the stated net."""
    gw = seed_gateway(conn, fee_bps=0, fee_fixed_minor=0)   # fees aren't what this test checks
    seed_ledger(conn, "cap1", gw, 1000, D0, "capture")
    seed_ledger(conn, "ref1", gw, -3000, D0, "refund")
    seed_payout(conn, "po1", gw, D0, 1000 - 3000)
    seed_settlement(conn, gw, "cap1", 1000, 0, D0, payout_ref="po1", line_type="capture")
    seed_settlement(conn, gw, "ref1", -3000, 0, D0, payout_ref="po1", line_type="refund")
    stats = run_match(conn, gw, D0)
    assert stats.exceptions == 0
    assert stats.matched == 2


def test_capture_and_its_refund_cannot_share_a_txn_ref(conn):
    """The schema enforces this, not the matcher: txn_ref is ledger_transactions'
    primary key, so a refund of a captured txn must be given its own ref."""
    gw = seed_gateway(conn)
    seed_ledger(conn, "t1", gw, 1000, D0, "capture")
    with pytest.raises(psycopg.errors.UniqueViolation):
        seed_ledger(conn, "t1", gw, -1000, D0, "refund")


def test_unknown_gateway_raises(conn):
    with pytest.raises(ValueError, match="unknown gateway_id"):
        run_match(conn, "does_not_exist", D0)
