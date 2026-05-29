"""Smoke test: the postgis fixture comes up and we can talk to it."""
import psycopg
import pytest


@pytest.mark.integration
async def test_db_url_yields_a_working_postgis_database(db_url: str) -> None:
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT postgis_version()")
            row = await cur.fetchone()
            assert row is not None
            assert row[0]  # non-empty version string
            await cur.execute("SELECT count(*) FROM app.cities")
            count = await cur.fetchone()
            assert count == (3,)
