from pathlib import Path

import pytest

DBC = Path(__file__).resolve().parents[2] / "can" / "ME1_4.dbc"


@pytest.fixture
def dbc_path() -> Path:
    return DBC
