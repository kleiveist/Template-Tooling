"""Tauri desktop-shell feature adapter."""

from tools.adapters.base import (
    AdapterActionResult,
    AdapterCapability,
    BaseAdapter,
    PathRequirement,
    project_relative_path,
)
from tools.core.context import ProjectContext
from tools.integration.model import Ownership


class TauriAdapter(BaseAdapter):
    name = "tauri"
    feature_ids = ("tauri",)
    capabilities = frozenset(AdapterCapability)

    def install(self, context: ProjectContext) -> AdapterActionResult:
        return self._run_control_action(context, AdapterCapability.INSTALL)

    def run(self, context: ProjectContext) -> AdapterActionResult:
        return self._run_control_action(context, AdapterCapability.RUN)

    def stop(self, context: ProjectContext) -> AdapterActionResult:
        return self._run_control_action(context, AdapterCapability.STOP)

    def test(self, context: ProjectContext) -> AdapterActionResult:
        return self._run_control_action(context, AdapterCapability.TEST)

    def build(self, context: ProjectContext) -> AdapterActionResult:
        return self._run_control_action(context, AdapterCapability.BUILD)

    def requirements(self, context: ProjectContext) -> tuple[PathRequirement, ...]:
        root = context.paths.tauri
        requirements = []
        if root != context.project_root:
            requirements.append(
                PathRequirement(
                    path=project_relative_path(context, root),
                    ownership=Ownership.PROJECT,
                    kind="directory",
                    required=False,
                    reason="Tauri feature root",
                )
            )
        requirements.extend(
            (
                PathRequirement(
                    path=project_relative_path(context, root / "Cargo.toml"),
                    ownership=Ownership.PROJECT,
                    kind="file",
                    required=False,
                    reason="Tauri Rust package marker",
                ),
                PathRequirement(
                    path=project_relative_path(context, root / "tauri.conf.json"),
                    ownership=Ownership.PROJECT,
                    kind="file",
                    required=False,
                    reason="Tauri configuration marker",
                    marker=True,
                ),
            )
        )
        return tuple(requirements)


__all__ = ["TauriAdapter"]
