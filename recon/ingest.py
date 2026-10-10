"""Ingest a gateway settlement file.

File format (simulated): CSV, one file per gateway per day. First row is the
payout summary, remaining rows are detail lines belonging to it.

    row_type,payout_ref,gateway_id,payout_date,net_amount_minor,line_count
    payout,po_8841,gw_alpha,2026-10-05,118400,6
    row_type,txn_ref,line_type,amount_minor,fee_minor,settled_on
    detail,txn_a1,capture,50000,150,2026-10-05
    detail,txn_a2,capture,30000,90,2026-10-05
    ...

Ingestion is idempotent by file content hash (sha256): re-running the same
file is a no-op. A file with a name already ingested for that gateway, but
different bytes, is rejected rather than silently overwritten -- that's a
data problem for a human, not something to paper over.
"""
import csv
import hashlib
import io
from dataclasses import dataclass

import psycopg


class IngestError(Exception):
    pass


@dataclass
class IngestResult:
    file_name: str
    gateway_id: str
    payout_ref: str
    detail_rows: int
    skipped_duplicate: bool = False


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def ingest_file(conn: psycopg.Connection, gateway_id: str, file_name: str, raw: bytes) -> IngestResult:
    """Parse and load one settlement file. Caller commits."""
    digest = _sha256(raw)

    existing = conn.execute(
        "SELECT sha256, row_count FROM ingested_files WHERE gateway_id = %s AND file_name = %s",
        (gateway_id, file_name),
    ).fetchone()
    if existing is not None:
        prior_sha, prior_rows = existing
        if prior_sha == digest:
            return IngestResult(file_name, gateway_id, payout_ref="", detail_rows=prior_rows,
                               skipped_duplicate=True)
        raise IngestError(
            f"{file_name}: already ingested for {gateway_id} with different content "
            f"(prior sha256={prior_sha[:12]}..., new sha256={digest[:12]}...) -- "
            "a settlement file must never be silently overwritten")

    lines = raw.decode("utf-8").splitlines()
    if len(lines) < 2:
        raise IngestError(f"{file_name}: expected a payout row and a header row, got {len(lines)} lines")

    payout_row = next(csv.DictReader([lines[0], lines[1]]))
    required = {"payout_ref", "gateway_id", "payout_date", "net_amount_minor", "line_count"}
    missing = required - payout_row.keys()
    if missing:
        raise IngestError(f"{file_name}: payout row missing fields {missing}")
    if payout_row["gateway_id"] != gateway_id:
        raise IngestError(
            f"{file_name}: file says gateway_id={payout_row['gateway_id']!r} but was "
            f"ingested as {gateway_id!r}")

    payout_ref = payout_row["payout_ref"]
    detail_reader = csv.DictReader(io.StringIO("\n".join(lines[2:])))
    detail_rows = list(detail_reader)

    conn.execute(
        "INSERT INTO payouts (payout_ref, gateway_id, payout_date, net_amount_minor, line_count) "
        "VALUES (%s,%s,%s,%s,%s) "
        "ON CONFLICT (payout_ref) DO UPDATE SET "
        "  net_amount_minor = EXCLUDED.net_amount_minor, line_count = EXCLUDED.line_count",
        (payout_ref, gateway_id, payout_row["payout_date"],
         int(payout_row["net_amount_minor"]), int(payout_row["line_count"])),
    )

    for i, row in enumerate(detail_rows):
        required_detail = {"txn_ref", "line_type", "amount_minor", "fee_minor", "settled_on"}
        missing = required_detail - row.keys()
        if missing:
            raise IngestError(f"{file_name}: detail row {i} missing fields {missing}")
        conn.execute(
            "INSERT INTO settlements "
            "(gateway_id, file_name, txn_ref, settled_amount_minor, settled_fee_minor, settled_on, "
            " payout_ref, line_type) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (gateway_id, file_name, row["txn_ref"], int(row["amount_minor"]), int(row["fee_minor"]),
             row["settled_on"], payout_ref, row["line_type"]),
        )

    conn.execute(
        "INSERT INTO ingested_files (file_name, gateway_id, sha256, row_count) VALUES (%s,%s,%s,%s)",
        (file_name, gateway_id, digest, len(detail_rows)),
    )
    return IngestResult(file_name, gateway_id, payout_ref, len(detail_rows))
