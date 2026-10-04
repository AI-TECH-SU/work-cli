"""Translate legacy project tables into the shared application resource model."""

from __future__ import annotations

from workspace_manager.errors import WorkError
from workspace_manager.extensions import load_extension, validate_options
from workspace_manager.models import AppConfig, ProjectConfig


def configured_apps(project: ProjectConfig) -> tuple[AppConfig, ...]:
    """Return one application list for both old and specification-based configs.

    Legacy identifiers retain their saved-window and instance-storage names.
    The config loader makes an explicit apps list authoritative and separately
    rejects two explicitly enabled definitions of the same extension.
    """
    explicit_extensions = {app.extension for app in project.apps if app.extension}
    apps: list[AppConfig] = []
    if project.vscode.enabled and "vscode" not in explicit_extensions:
        workspace = project.vscode.workspace or project.root
        options: dict[str, object] = {"workspace": str(workspace)}
        if project.vscode.profile:
            options["profile"] = project.vscode.profile
        context = project.context.path
        if project.context.open_in_vscode and context is not None and context != workspace:
            options["context"] = str(context)
        apps.append(AppConfig(
            name="vscode",
            extension="vscode",
            options=validate_options(load_extension("vscode"), options, project.root),
            launch_mode="instance" if project.vscode.separate_instance else project.vscode.launch_mode,
            reuse_existing=project.vscode.reuse_existing,
            legacy_slot="vscode",
        ))
    if project.chrome.enabled and "chrome" not in explicit_extensions:
        options = {
            "profile": project.chrome.profile,
            "urls": list(project.chrome.urls),
            "reopen_on_restore": project.chrome.reopen_on_restore,
        }
        apps.append(AppConfig(
            name="chrome",
            extension="chrome",
            options=validate_options(load_extension("chrome"), options, project.root),
            launch_mode=project.chrome.launch_mode,
            reuse_existing=project.chrome.reuse_existing,
            legacy_slot="chrome",
            restore_missing=project.chrome.reopen_on_restore,
        ))
    apps.extend(project.apps)
    names: set[str] = set()
    for app in apps:
        if app.name in names:
            raise WorkError(
                f"duplicate app name: {app.name!r}; choose unique [[apps]] names or "
                "disable the corresponding legacy app section"
            )
        names.add(app.name)
    return tuple(apps)
