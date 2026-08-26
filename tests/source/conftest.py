from __future__ import annotations

from pathlib import Path

import pytest

from tools.quality.config import DEFAULT_CONFIG_PATH, load_quality_config
from tools.quality.model import QualityConfig

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def quality_config() -> QualityConfig:
    return load_quality_config(DEFAULT_CONFIG_PATH, project_root=REPOSITORY_ROOT)
