import pytest


@pytest.mark.unit
def test_pytest_smoke() -> None:
    """Technical smoke test for the initial pytest setup."""
    assert True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_asyncio_smoke() -> None:
    """Verify that pytest-asyncio is configured and working."""
    assert True
