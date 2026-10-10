"""Settlement file ingest: idempotency, rejection of silent overwrites,
split payouts, and negative-net payouts."""
import pytest

from recon.ingest import ingest_file, IngestError
from tests.conftest import seed_gateway


def _file(payout_ref, gateway_id, payout_date, net, line_count, details):
    header = "row_type,payout_ref,gateway_id,payout_date,net_amount_minor,line_count\n"
    payout = f"payout,{payout_ref},{gateway_id},{payout_date},{net},{line_count}\n"
    detail_header = "row_type,txn_ref,line_type,amount_minor,fee_minor,settled_on\n"
    rows = "".join(
        f"detail,{txn},{ltype},{amt},{fee},{settled}\n"
        for txn, ltype, amt, fee, settled in details
    )
    return (header + payout + detail_header + rows).encode("utf-8")


def test_clean_file_ingests_payout_and_detail_rows(conn):
    gw = seed_gateway(conn)
    raw = _file("po_1", gw, "2026-10-05", 10000, 2, [
        ("txn_1", "capture", 5000, 15, "2026-10-05"),
        ("txn_2", "capture", 5000, 15, "2026-10-05"),
    ])
    result = ingest_file(conn, gw, "gw_test_2026-10-05.csv", raw)
    assert result.detail_rows == 2
    assert result.skipped_duplicate is False
    payout = conn.execute("SELECT net_amount_minor FROM payouts WHERE payout_ref = 'po_1'").fetchone()
    assert payout == (10000,)
    rows = conn.execute("SELECT count(*) FROM settlements WHERE payout_ref = 'po_1'").fetchone()
    assert rows == (2,)


def test_reingesting_identical_bytes_is_a_noop(conn):
    gw = seed_gateway(conn)
    raw = _file("po_2", gw, "2026-10-05", 5000, 1, [("txn_1", "capture", 5000, 15, "2026-10-05")])
    ingest_file(conn, gw, "f.csv", raw)
    result = ingest_file(conn, gw, "f.csv", raw)
    assert result.skipped_duplicate is True
    rows = conn.execute("SELECT count(*) FROM settlements WHERE payout_ref = 'po_2'").fetchone()
    assert rows == (1,)                                   # not doubled


def test_same_file_name_different_content_is_rejected(conn):
    gw = seed_gateway(conn)
    ingest_file(conn, gw, "f.csv",
               _file("po_3", gw, "2026-10-05", 5000, 1, [("txn_1", "capture", 5000, 15, "2026-10-05")]))
    with pytest.raises(IngestError, match="different content"):
        ingest_file(conn, gw, "f.csv",
                   _file("po_3", gw, "2026-10-05", 9999, 1, [("txn_1", "capture", 9999, 15, "2026-10-05")]))
    # the original must survive untouched
    payout = conn.execute("SELECT net_amount_minor FROM payouts WHERE payout_ref = 'po_3'").fetchone()
    assert payout == (5000,)


def test_split_payout_two_files_same_day(conn):
    """A day's activity settled across two separate payout batches."""
    gw = seed_gateway(conn)
    ingest_file(conn, gw, "morning.csv",
               _file("po_4a", gw, "2026-10-05", 3000, 1, [("txn_1", "capture", 3000, 10, "2026-10-05")]))
    ingest_file(conn, gw, "evening.csv",
               _file("po_4b", gw, "2026-10-05", 4000, 1, [("txn_2", "capture", 4000, 10, "2026-10-05")]))
    payouts = conn.execute(
        "SELECT payout_ref, net_amount_minor FROM payouts WHERE payout_date = '2026-10-05' "
        "AND gateway_id = %s ORDER BY payout_ref", (gw,)).fetchall()
    assert payouts == [("po_4a", 3000), ("po_4b", 4000)]


def test_negative_net_payout_refunds_exceeded_captures(conn):
    """Refunds exceeded captures for the period: a real, valid case, not an error."""
    gw = seed_gateway(conn)
    raw = _file("po_5", gw, "2026-10-05", -2000, 2, [
        ("txn_1", "capture", 1000, 5, "2026-10-05"),
        ("txn_2", "refund", -3000, 0, "2026-10-05"),
    ])
    result = ingest_file(conn, gw, "f.csv", raw)
    assert result.detail_rows == 2
    payout = conn.execute("SELECT net_amount_minor FROM payouts WHERE payout_ref = 'po_5'").fetchone()
    assert payout == (-2000,)


def test_unknown_gateway_id_in_file_is_rejected(conn):
    gw = seed_gateway(conn)
    raw = _file("po_6", "some_other_gateway", "2026-10-05", 1000, 1,
               [("txn_1", "capture", 1000, 5, "2026-10-05")])
    with pytest.raises(IngestError, match="but was ingested as"):
        ingest_file(conn, gw, "f.csv", raw)
