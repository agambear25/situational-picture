"""Hermetic offline test fixtures — no Ollama, no DB. Frozen verdicts stand in for the model."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ingest.contract import Observation
from llm.cache import FrozenVerdictCache
from llm.backend import FrozenBackend

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def observations() -> list[Observation]:
    data = yaml.safe_load((FIX / "synthetic_v1.yaml").read_text())
    return [Observation.from_fixture(d) for d in data["observations"]]


@pytest.fixture
def ground_truth() -> dict:
    return yaml.safe_load((FIX / "ground_truth_v1.yaml").read_text())


@pytest.fixture
def frozen_cache() -> FrozenVerdictCache:
    return FrozenVerdictCache(FIX / "verdicts_v1.json")


@pytest.fixture
def frozen_backend() -> FrozenBackend:
    return FrozenBackend()
