import os

import psycopg
import pytest

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5434/recon")


@pytest.fixture
def conn():
    with psycopg.connect(DATABASE_URL, autocommit=False) as c:
        yield c
        c.rollback()


def seed_gateway(conn, gateway_id="gw_test", fee_bps=200, fee_fixed_minor=10, sla_days=2):
    conn.execute(
        "INSERT INTO gateways (gateway_id, fee_bps, fee_fixed_minor, settlement_sla_days) "
        "VALUES (%s,%s,%s,%s) ON CONFLICT (gateway_id) DO NOTHING",
        (gateway_id, fee_bps, fee_fixed_minor, sla_days),
    )
    return gateway_id


def seed_ledger(conn, txn_ref, gateway_id, amount_minor, captured_on, entry_type="capture"):
    conn.execute(
        "INSERT INTO ledger_transactions (txn_ref, gateway_id, amount_minor, captured_on, entry_type) "
        "VALUES (%s,%s,%s,%s,%s)",
        (txn_ref, gateway_id, amount_minor, captured_on, entry_type),
    )


def seed_payout(conn, payout_ref, gateway_id, payout_date, net_amount_minor, line_count=0):
    conn.execute(
        "INSERT INTO payouts (payout_ref, gateway_id, payout_date, net_amount_minor, line_count) "
        "VALUES (%s,%s,%s,%s,%s)",
        (payout_ref, gateway_id, payout_date, net_amount_minor, line_count),
    )


def seed_settlement(conn, gateway_id, txn_ref, amount_minor, fee_minor, settled_on,
                    payout_ref=None, line_type="capture", file_name="seed.csv"):
    conn.execute(
        "INSERT INTO settlements (gateway_id, file_name, txn_ref, settled_amount_minor, "
        "settled_fee_minor, settled_on, payout_ref, line_type) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (gateway_id, file_name, txn_ref, amount_minor, fee_minor, settled_on, payout_ref, line_type),
    )
