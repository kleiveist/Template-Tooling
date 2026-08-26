"""Portable tooling-documentation adapter."""

from tools.adapters.base import BaseAdapter, PathRequirement, project_relative_path
from tools.core.context import ProjectContext
from tools.integration.model import Ownership


class DocumentationAdapter(BaseAdapter):
    name = "documentation"
    core = True

    def requirements(self, context: ProjectContext) -> tuple[PathRequirement, ...]:
        return (
            PathRequirement(
                path=project_relative_path(context, context.docs_root),
                ownership=Ownership.TOOLING,
                kind="directory",
                reason="portable tooling documentation root",
            ),
        )


__all__ = ["DocumentationAdapter"]
