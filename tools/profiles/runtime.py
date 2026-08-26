from __future__ import annotations

from pathlib import Path

from tools.profiles.loader import load_active_profile
from tools.profiles.model import ProjectProfile

ROOT = Path(__file__).resolve().parents[2]


def active_profile(project_root: Path | None = None) -> ProjectProfile:
    return load_active_profile(project_root or ROOT)


def feature_enabled(feature_id: str, project_root: Path | None = None) -> bool:
    return active_profile(project_root or ROOT).has_feature(feature_id)
