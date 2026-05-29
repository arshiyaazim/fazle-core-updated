"""Unit-layer conftest: auto-clean tables before every test that uses the DB."""
import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def _clean_for_unit(clean_tables):
    """Delegate to the root clean_tables fixture (autouse for unit tests)."""
    yield
