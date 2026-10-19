import sys
from pathlib import Path

# Make the backend package importable when pytest is launched as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest


@pytest.fixture(autouse=True)
def isolate_deployment_choice(monkeypatch):
    """Offline tests must not inherit an operator's deployment selection."""
    monkeypatch.setenv("DIETITIAN_ID", "clinic_001")
