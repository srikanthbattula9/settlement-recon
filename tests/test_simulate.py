"""Simulator: exact behavior of the low-level builders (deterministic,
explicit refs), determinism of the random layer, and two end-to-end runs
through ingest + match."""
from datetime import date, timedelta

from recon.match import run_match
from recon.simulate import (
    ChaosRates, GATEWAYS, LedgerRow, build_settlement_lines, ensure_gateways,
    make_captures, random_chaos, run_demo_day, split_into_payouts,
)

D0 = date(2026, 10, 5)


def test_missing_refs_are_excluded_from_the_file():
    rows = [LedgerRow("t1", 1000, D0), LedgerRow("t2", 2000, D0)]
    lines = build_settlement_lines(rows, 0, 0, settled_on=D0, missing_refs={"t1"})
    assert [l["txn_ref"] for l in lines] == ["t2"]


def test_duplicate_refs_produce_two_identical_lines():
    rows = [LedgerRow("t1", 1000, D0)]
    lines = build_settlement_lines(rows, 0, 0, settled_on=D0, duplicate_refs={"t1"})
    assert len(lines) == 2
    assert lines[0] == lines[1]


def test_amount_and_fee_mismatch_applied_only_to_targeted_refs():
    rows = [LedgerRow("t1", 1000, D0), LedgerRow("t2", 1000, D0)]
    lines = build_settlement_lines(rows, 200, 10, settled_on=D0,
                                   amount_mismatch_refs={"t1"}, fee_mismatch_refs={"t2"})
    by_ref = {l["txn_ref"]: l for l in lines}
    assert by_ref["t1"]["amount_minor"] == 1777                        # 1000 + 777
    assert by_ref["t2"]["amount_minor"] == 1000                        # untouched
    expected_fee = (1000 * 200) // 10_000 + 10
    assert by_ref["t1"]["fee_minor"] == expected_fee                   # untouched
    assert by_ref["t2"]["fee_minor"] == expected_fee + 111


def test_late_refs_settle_after_the_given_offset():
    rows = [LedgerRow("t1", 1000, D0)]
    lines = build_settlement_lines(rows, 0, 0, settled_on=D0, late_refs={"t1"}, late_by_days=7)
    assert lines[0]["settled_on"] == (D0 + timedelta(days=7)).isoformat()


def test_unknown_count_appends_blank_ref_lines():
    lines = build_settlement_lines([], 0, 0, settled_on=D0, unknown_count=3)
    assert len(lines) == 3
    assert all(l["txn_ref"] == "" for l in lines)


def test_split_into_payouts_is_round_robin_and_preserves_all_lines():
    lines = [{"txn_ref": f"t{i}"} for i in range(5)]
    buckets = split_into_payouts(lines, 2)
    assert [l["txn_ref"] for l in buckets[0]] == ["t0", "t2", "t4"]
    assert [l["txn_ref"] for l in buckets[1]] == ["t1", "t3"]
    assert sum(len(b) for b in buckets) == 5


def test_random_chaos_is_deterministic_for_a_given_seed():
    import random
    rows = make_captures(random.Random(1), "gw_alpha", D0, 30)
    c1 = random_chaos(random.Random(42), rows, ChaosRates(missing=0.2, refund=0.2))
    c2 = random_chaos(random.Random(42), rows, ChaosRates(missing=0.2, refund=0.2))
    assert c1 == c2
    assert c1["missing"] or c1["refund"]                    # the seed/rate actually produced something


def test_chaos_buckets_for_one_ref_are_mutually_exclusive():
    """missing/duplicate/amount_mismatch/fee_mismatch/late never overlap for
    the same ref -- random_chaos breaks on first match."""
    import random
    rows = make_captures(random.Random(1), "gw_alpha", D0, 200)
    c = random_chaos(random.Random(7), rows,
                     ChaosRates(missing=0.2, duplicate=0.2, amount_mismatch=0.2,
                               fee_mismatch=0.2, late=0.2))
    mismatch_buckets = ["missing", "duplicate", "amount_mismatch", "fee_mismatch", "late"]
    seen = {}
    for name in mismatch_buckets:
        for ref in c[name]:
            assert ref not in seen, f"{ref} assigned to both {seen.get(ref)} and {name}"
            seen[ref] = name


def test_run_demo_day_with_no_chaos_is_completely_clean(conn):
    ensure_gateways(conn)
    stats = run_demo_day(conn, "gw_alpha", D0, 25, ChaosRates(), seed=0)
    assert stats.exceptions == 0
    assert stats.matched == 25


def test_run_demo_day_with_chaos_produces_only_known_exception_classes(conn):
    ensure_gateways(conn)
    rates = ChaosRates(missing=0.1, duplicate=0.1, amount_mismatch=0.1, fee_mismatch=0.1,
                       late=0.1, refund=0.2, unknown_lines=2, split_payout=True,
                       bad_payout_total=True)
    stats = run_demo_day(conn, "gw_beta", D0, 100, rates, seed=3)
    known = {"missing_in_settlement", "missing_in_ledger", "amount_mismatch", "fee_mismatch",
            "duplicate_settlement", "timing_lag", "payout_total_mismatch", "unknown_line"}
    assert stats.exceptions > 0
    assert set(stats.by_class) <= known
    assert sum(stats.by_class.values()) == stats.exceptions
    # every class this chaos targets should show up somewhere with n=100 at these rates
    assert "unknown_line" in stats.by_class                 # deterministic: unknown_lines=2 always fires
    assert "payout_total_mismatch" in stats.by_class         # deterministic: bad_payout_total=True always fires


def test_run_demo_day_is_deterministic_for_a_given_seed(conn):
    ensure_gateways(conn)
    rates = ChaosRates(missing=0.1, late=0.1)
    stats1 = run_demo_day(conn, "gw_gamma", D0, 40, rates, seed=99)
    conn.rollback()
    ensure_gateways(conn)
    stats2 = run_demo_day(conn, "gw_gamma", D0, 40, rates, seed=99)
    assert stats1.by_class == stats2.by_class
    assert stats1.matched == stats2.matched
