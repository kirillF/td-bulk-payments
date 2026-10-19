"""Frozen pre-Alembic schema verification for explicit legacy adoption only."""

from collections.abc import Generator
from typing import Any

import psycopg
from psycopg.rows import DictRow, dict_row


def _legacy_checks() -> Generator[tuple[str, tuple[object, ...]], list[DictRow]]:
    """Frozen baseline checks used only by legacy adoption migrations."""
    columns = {
        "firms": {
            "id": ("integer", True),
            "name": ("text", True),
            "balance_cents": ("integer", True),
            "uuid": ("text", True),
        },
        "payments": {
            "id": ("integer", True),
            "payer_firm_id": ("integer", True),
            "payee_firm_id": ("integer", True),
            "amount_cents": ("integer", True),
            "description": ("text", True),
        },
        "bulk_payment_requests": {
            "payer_firm_uuid": ("text", True),
            "idempotency_key": ("text", True),
            "request_fingerprint": ("text", True),
            "status_code": ("integer", False),
            "response_body": ("jsonb", False),
            "created_at": ("timestamp with time zone", True),
        },
    }
    constraints = {
        "firms": {
            "PRIMARY KEY (id)",
            "CHECK (balance_cents >= 0)",
            "CHECK (uuid::uuid::text = uuid)",
        },
        "payments": {
            "PRIMARY KEY (id)",
            "CHECK (amount_cents > 0)",
            "CHECK (payer_firm_id <> payee_firm_id)",
            "FOREIGN KEY (payer_firm_id) REFERENCES firms(id)",
            "FOREIGN KEY (payee_firm_id) REFERENCES firms(id)",
        },
        "bulk_payment_requests": {
            "PRIMARY KEY (payer_firm_uuid,idempotency_key)",
            "CHECK (length(idempotency_key) >= 1 AND length(idempotency_key) <= 128)",
            "CHECK (length(request_fingerprint) = 64)",
            "CHECK (status_code = ANY ARRAY[201,422])",
            "CHECK (status_code IS NULL = response_body IS NULL)",
        },
    }

    def normalized(value: str) -> str:
        return "".join(value.split()).replace("(", "").replace(")", "")

    for table, expected in columns.items():
        rows = yield (
            "SELECT attname,format_type(atttypid,atttypmod) AS type,attnotnull,attidentity "
            "FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped",
            (table,),
        )
        if {r["attname"]: (r["type"], r["attnotnull"]) for r in rows} != expected:
            raise ValueError("Incompatible database columns")
        if table in {"firms", "payments"} and not any(
            r["attname"] == "id" and r["attidentity"] == "d" for r in rows
        ):
            raise ValueError("Incompatible generated identity")
        checks = yield (
            "SELECT pg_get_constraintdef(oid) AS definition,convalidated "
            "FROM pg_constraint WHERE conrelid=%s::regclass",
            (table,),
        )
        if any(not row["convalidated"] for row in checks) or {
            normalized(row["definition"]) for row in checks
        } != {normalized(item) for item in constraints[table]}:
            raise ValueError("Incompatible database constraints")
    rows = yield (
        "SELECT attcollation='pg_catalog.\"C\"'::regcollation AS valid "
        "FROM pg_attribute WHERE attrelid='bulk_payment_requests'::regclass "
        "AND attname='idempotency_key' AND NOT attisdropped",
        (),
    )
    if not rows or not rows[0]["valid"]:
        raise ValueError("Incompatible idempotency key collation")
    rows = yield (
        "SELECT i.indisunique AND i.indisvalid AND i.indpred IS NULL AND i.indnkeyatts=1 "
        "AND i.indrelid='firms'::regclass AND i.indkey[0]=(SELECT attnum FROM pg_attribute "
        "WHERE attrelid='firms'::regclass AND attname='uuid') AND am.amname='btree' AS valid "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
        "JOIN pg_am am ON am.oid=c.relam WHERE i.indexrelid='firms_uuid_idx'::regclass",
        (),
    )
    if not rows or not rows[0]["valid"]:
        raise ValueError("Required unique firm UUID index missing")
    for table in ("firms", "payments"):
        rows = yield ("SELECT pg_get_serial_sequence(%s,'id') AS sequence", (table,))
        if not rows or not rows[0]["sequence"]:
            raise ValueError("Generated identity sequence missing")
    rows = yield (
        "SELECT pg_get_expr(d.adbin,d.adrelid) AS expression FROM pg_attrdef d "
        "JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum "
        "WHERE a.attrelid='bulk_payment_requests'::regclass AND a.attname='created_at'",
        (),
    )
    if len(rows) != 1 or rows[0]["expression"] != "now()":
        raise ValueError("Incompatible request timestamp default")


def validate_legacy_schema(connection: psycopg.Connection[Any]) -> None:
    checks = _legacy_checks()
    query = next(checks)
    with connection.cursor(row_factory=dict_row) as cursor:
        while True:
            rows = cursor.execute(*query).fetchall()
            try:
                query = checks.send(rows)
            except StopIteration:
                return
