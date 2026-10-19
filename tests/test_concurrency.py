"""Independent instances and actual observed PostgreSQL lock overlap."""

import concurrent.futures
from threading import Barrier

import psycopg

from .conftest import OTHER, PAYEE, PAYER, balances, count, payload, post, running_app
from .test_payments import wait_for_blocked_requests


def test_opposing_instances_observe_real_contention(seeded_database):
    with psycopg.connect(seeded_database) as connection:
        connection.execute("UPDATE firms SET balance_cents=10000")
    with running_app(seeded_database) as first, running_app(seeded_database) as second:
        assert first.state.pool is not second.state.pool
        barrier = Barrier(3)
        firms = [PAYER, PAYEE, OTHER]

        def submit(index):
            barrier.wait(timeout=3)
            return post(
                first if index % 2 else second,
                payload("100", payer=firms[index], payee=firms[(index + 1) % 3]),
                f"opposing-{index}",
            )

        with psycopg.connect(seeded_database) as blocker:
            blocker.execute("SELECT id FROM firms ORDER BY id FOR UPDATE")
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(submit, i) for i in range(3)]
                try:
                    wait_for_blocked_requests(seeded_database, 3)
                finally:
                    blocker.commit()
                results = [f.result(timeout=10) for f in futures]
        assert [r.status_code for r in results] == [201] * 3
        assert balances(seeded_database) == {PAYER: 10000, PAYEE: 10000, OTHER: 10000}
        assert count(seeded_database, "payments") == 3
