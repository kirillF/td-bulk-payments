"""Explicit request lifecycle, fenced execution and atomic business receipts."""

from uuid import uuid4

import psycopg
from alembic import op
from alembic.util import CommandError
from psycopg.rows import dict_row

from app.db.database import validate_schema_sync
from app.schemas.idempotency import validate_result

revision = "0002_request_lifecycle"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    driver = connection.connection.driver_connection
    if not isinstance(driver, psycopg.Connection):
        raise CommandError("Unsupported migration connection")
    validate_schema_sync(driver, lifecycle=False)
    # The same transaction both validates old outcomes and creates their new metadata.
    # Streaming bounds Python memory for a large retained-key population.
    with driver.cursor(name="legacy_results", row_factory=dict_row) as cursor:
        cursor.execute("SELECT status_code,response_body FROM bulk_payment_requests")
        for row in cursor:
            validate_result(row["status_code"], row["response_body"])
    connection.exec_driver_sql("""
        ALTER TABLE bulk_payment_requests
            ADD COLUMN state TEXT NOT NULL DEFAULT 'completed',
            ADD COLUMN generation UUID,
            ADD COLUMN owner_token UUID,
            ADD COLUMN owner_deadline TIMESTAMPTZ NOT NULL DEFAULT now(),
            ADD COLUMN completed_at TIMESTAMPTZ DEFAULT now(),
            ADD COLUMN expires_at TIMESTAMPTZ NOT NULL DEFAULT (now()+interval '24 hours');
    """)
    with driver.cursor(name="legacy_identities", row_factory=dict_row) as cursor:
        cursor.execute("SELECT payer_firm_uuid,idempotency_key FROM bulk_payment_requests")
        for row in cursor:
            connection.exec_driver_sql(
                "UPDATE bulk_payment_requests SET generation=%s,owner_token=%s "
                "WHERE payer_firm_uuid=%s AND idempotency_key=%s",
                (uuid4(), uuid4(), row["payer_firm_uuid"], row["idempotency_key"]),
            )
    connection.exec_driver_sql("""
        ALTER TABLE bulk_payment_requests
            ALTER COLUMN generation SET NOT NULL,
            ALTER COLUMN owner_token SET NOT NULL,
            ALTER COLUMN state DROP DEFAULT,
            ALTER COLUMN owner_deadline DROP DEFAULT,
            ALTER COLUMN completed_at DROP DEFAULT,
            ALTER COLUMN expires_at DROP DEFAULT,
            ADD UNIQUE (generation),
            ADD UNIQUE (payer_firm_uuid,idempotency_key,generation),
            ADD CHECK (state IN ('processing','completed')),
            ADD CHECK (
                (state='processing' AND status_code IS NULL AND response_body IS NULL
                 AND completed_at IS NULL)
                OR (state='completed' AND status_code IS NOT NULL AND response_body IS NOT NULL
                    AND completed_at IS NOT NULL)
            );
        CREATE INDEX request_expiry_idx ON bulk_payment_requests (expires_at);
        CREATE INDEX request_processing_deadline_idx ON bulk_payment_requests (owner_deadline)
            WHERE state='processing';
        CREATE TABLE request_execution_guards (
            generation UUID PRIMARY KEY,
            payer_firm_uuid TEXT NOT NULL,
            idempotency_key TEXT COLLATE "C" NOT NULL,
            FOREIGN KEY (payer_firm_uuid,idempotency_key,generation)
                REFERENCES bulk_payment_requests (payer_firm_uuid,idempotency_key,generation)
                ON DELETE CASCADE
        );
        CREATE TABLE request_result_receipts (
            generation UUID PRIMARY KEY REFERENCES request_execution_guards (generation)
                ON DELETE CASCADE,
            owner_token UUID NOT NULL,
            request_fingerprint TEXT NOT NULL CHECK (length(request_fingerprint)=64),
            status_code INTEGER NOT NULL CHECK (status_code IN (201,422)),
            response_body JSONB NOT NULL,
            committed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        INSERT INTO request_execution_guards (generation,payer_firm_uuid,idempotency_key)
            SELECT generation,payer_firm_uuid,idempotency_key FROM bulk_payment_requests;
        INSERT INTO request_result_receipts
            (generation,owner_token,request_fingerprint,status_code,response_body)
            SELECT generation,owner_token,request_fingerprint,status_code,response_body
            FROM bulk_payment_requests;
    """)


def downgrade() -> None:
    raise CommandError("Destructive request lifecycle downgrade is unsupported")
