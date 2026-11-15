from concurrent.futures import ThreadPoolExecutor

from app.core.database import Database, get_database


def test_parallel_requests_share_pool_but_not_sessions():
    Database.reset_instance()
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            databases = list(executor.map(lambda _: get_database(), range(32)))
        assert all(database is databases[0] for database in databases)
        with databases[0].session() as first, databases[0].session() as second:
            assert first is not second
            assert first.bind is second.bind
    finally:
        Database.reset_instance()
