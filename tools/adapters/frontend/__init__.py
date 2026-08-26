"""Frontend feature adapter."""

from tools.adapters.base import BaseAdapter, PathRequirement, project_relative_path
from tools.core.context import ProjectContext
from tools.integration.model import Ownership


class FrontendAdapter(BaseAdapter):
    name = "frontend"
    feature_ids = ("frontend",)

    def requirements(self, context: ProjectContext) -> tuple[PathRequirement, ...]:
        root = context.paths.frontend
        requirements = []
        if root != context.project_root:
            requirements.append(
                PathRequirement(
                    path=project_relative_path(context, root),
                    ownership=Ownership.PROJECT,
                    kind="directory",
                    required=False,
                    reason="frontend feature root",
                )
            )
        requirements.extend(
            (
                PathRequirement(
                    path=project_relative_path(context, root / "package.json"),
                    ownership=Ownership.PROJECT,
                    kind="file",
                    required=False,
                    reason="frontend package marker",
                    marker=True,
                    marker_json_keys=("dependencies.vite", "devDependencies.vite"),
                    marker_script_commands=("vite",),
                ),
                PathRequirement(
                    path=project_relative_path(context, root / "vite.config.ts"),
                    ownership=Ownership.PROJECT,
                    kind="file",
                    required=False,
                    reason="Vite configuration marker",
                    marker=True,
                ),
                PathRequirement(
                    path=project_relative_path(context, root / "vite.config.js"),
                    ownership=Ownership.PROJECT,
                    kind="file",
                    required=False,
                    reason="Vite JavaScript configuration marker",
                    marker=True,
                ),
                PathRequirement(
                    path=project_relative_path(context, root / "vite.config.cjs"),
                    ownership=Ownership.PROJECT,
                    kind="file",
                    required=False,
                    reason="Vite CommonJS configuration marker",
                    marker=True,
                ),
                PathRequirement(
                    path=project_relative_path(context, root / "vite.config.mjs"),
                    ownership=Ownership.PROJECT,
                    kind="file",
                    required=False,
                    reason="Vite ESM configuration marker",
                    marker=True,
                ),
                PathRequirement(
                    path=project_relative_path(context, root / "vite.config.cts"),
                    ownership=Ownership.PROJECT,
                    kind="file",
                    required=False,
                    reason="Vite CommonJS TypeScript configuration marker",
                    marker=True,
                ),
                PathRequirement(
                    path=project_relative_path(context, root / "vite.config.mts"),
                    ownership=Ownership.PROJECT,
                    kind="file",
                    required=False,
                    reason="Vite ESM TypeScript configuration marker",
                    marker=True,
                ),
            )
        )
        return tuple(requirements)


__all__ = ["FrontendAdapter"]
