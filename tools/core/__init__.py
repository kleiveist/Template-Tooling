"""Portable filesystem and project-context primitives."""

from tools.core.context import (
    DOCS_ROOT,
    PROJECT_CONFIG,
    PROJECT_ROOT,
    RESOURCES_ROOT,
    STATE_ROOT,
    TOOLS_ROOT,
    ProjectContext,
    ProjectPaths,
    ResourcePaths,
    default_context,
    load_context,
)
from tools.core.project_config import (
    ProjectConfig,
    ProjectConfigError,
    ProjectPathConfig,
    create_project_config,
    default_project_config,
    load_project_config,
    render_project_config,
)

__all__ = [
    "DOCS_ROOT",
    "PROJECT_CONFIG",
    "PROJECT_ROOT",
    "RESOURCES_ROOT",
    "STATE_ROOT",
    "TOOLS_ROOT",
    "ProjectConfig",
    "ProjectConfigError",
    "ProjectContext",
    "ProjectPathConfig",
    "ProjectPaths",
    "ResourcePaths",
    "create_project_config",
    "default_context",
    "default_project_config",
    "load_context",
    "load_project_config",
    "render_project_config",
]
