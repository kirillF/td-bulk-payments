"""SC-17: representative selective UUID lookup and uniqueness at the SQL boundary."""

import psycopg
import pytest

from .conftest import PAYEE, PAYER


def test_unique_uuid_index_remains_selective(seeded_database):
    # Two UUIDs out of 10,003 firms model selective participant resolution, not a capacity SLA.
    with psycopg.connect(seeded_database) as connection:
        connection.execute(
            "INSERT INTO firms(name,uuid,balance_cents) "
            "SELECT 'fixture',md5(n::text)::uuid::text,0 FROM generate_series(1,10000)n"
        )
        connection.execute("ANALYZE firms")
        plan = connection.execute(
            "EXPLAIN (FORMAT JSON) SELECT id FROM firms WHERE uuid=ANY(%s::text[])",
            ([PAYER, PAYEE],),
        ).fetchone()[0]
        assert "firms_uuid_idx" in str(plan)
        assert connection.execute(
            "SELECT id FROM firms WHERE uuid=ANY(%s::text[]) ORDER BY id", ([PAYER, PAYEE],)
        ).fetchall() == [(1,), (2,)]
    with (
        pytest.raises(psycopg.errors.UniqueViolation),
        psycopg.connect(seeded_database) as connection,
    ):
        connection.execute(
            "INSERT INTO firms(name,uuid,balance_cents) VALUES ('duplicate',%s,0)", (PAYER,)
        )
