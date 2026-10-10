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
