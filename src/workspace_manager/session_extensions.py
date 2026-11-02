"""Provider-neutral managed-session lifecycle driven by extension specifications."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from string import Formatter
import time
import uuid

from workspace_manager.errors import WorkError
from workspace_manager.extensions import (ExtensionSpec, _CONTEXT, _argv, _error, _keys,
    _option_value, _template, _text, _validate_inputs, _validate_install, validate_options, validate_input_paths)
from workspace_manager.models import AppConfig, CommandConfig, ProjectConfig
from workspace_manager.paths import state_home
from workspace_manager.system import run_interactive


_TOKENS = _CONTEXT | {"app", "session", "unit", "unit_root", "unit_command", "shell_command",
                       "shell", "view", "title", "title_prefix", "mode", "digest"}
_CORE = {"exists", "create", "list_units", "create_unit", "retain_unit", "start_unit", "stop", "attach"}
_VIEWS = {"view_exists", "view_owner", "view_attached", "view_mode", "view_create", "view_set_owner",
          "view_select", "view_detach", "view_attach", "view_set_mode", "view_list", "view_stop",
          "view_set_title", "view_enable_title"}
_MAPPINGS = {"identity_input": "string", "units_input": "records", "presentation_input": "string",
             "startup_command_input": "string", "startup_unit_input": "string", "startup_check_input": "string"}


def _inventory_schema(value: dict, fields: set[str], where: str) -> None:
    _keys(value, {"separator", "columns", "dead_value"}, where)
    separator = value.get("separator")
    if not isinstance(separator, str) or not separator or any(c in separator for c in "\r\n\0"):
        raise _error(f"{where}.separator must be a non-empty single-line delimiter")
    columns = value.get("columns")
    if not isinstance(columns, list) or len(columns) != len(fields) or not all(isinstance(column, str) for column in columns) or set(columns) != fields:
        raise _error(f"{where}.columns must contain {', '.join(sorted(fields))}")
    if "dead" in fields:
        _text(value.get("dead_value"), f"{where}.dead_value")
    elif "dead_value" in value:
        raise _error(f"{where}.dead_value is unsupported")


def validate_session_spec(data: dict, expected_id: str) -> ExtensionSpec:
    _keys(data, {"version", "id", "kind", "bundle_id", "command", "inputs", "session", "operations", "install"}, "root")
    if type(data.get("version")) is not int or data["version"] != 1:
        raise _error("version must be 1")
    if data.get("id") != expected_id:
        raise _error(f"id must match filename: {expected_id!r}")
    bundle = _text(data.get("bundle_id"), "bundle_id")
    inputs = data.get("inputs", {})
    _validate_inputs(inputs)
    command = _keys(data.get("command"), {"key", "name", "candidates"}, "command")
    if not command.get("key") and not command.get("name"):
        raise _error("command requires key or name")
    if "key" in command and (not isinstance(command["key"], str) or command["key"] not in CommandConfig.__dataclass_fields__):
        raise _error("command.key must name a configured command")
    if "name" in command:
        _text(command["name"], "command.name")
    if "candidates" in command:
        if not isinstance(command["candidates"], list):
            raise _error("command.candidates must be an array")
        for candidate in command["candidates"]:
            _text(candidate, "command.candidates")
    session = _keys(data.get("session"), set(_MAPPINGS) | {"default_unit", "inventory", "attach", "views"}, "session")
    for mapping, expected in _MAPPINGS.items():
        key = session.get(mapping)
        if not isinstance(key, str) or key not in inputs or inputs[key]["type"] != expected:
            raise _error(f"session.{mapping} must name a {expected} input")
    fields = inputs[session["units_input"]]["fields"]
    if set(fields) != {"name", "root", "command"} or any(fields[key]["type"] != kind for key, kind in
            (("name", "string"), ("root", "path"), ("command", "string"))):
        raise _error("session units require name:string, root:path, command:string fields")
    if not fields["name"].get("required") or not (fields["root"].get("required") or "default" in fields["root"]):
        raise _error("session unit name must be required and root required or defaulted")
    _text(session.get("default_unit"), "session.default_unit")
    _inventory_schema(session.get("inventory"), {"name", "dead"}, "session.inventory")
    attach = _keys(session.get("attach", {}), {"switch_env", "clear_env"}, "session.attach")
    if "switch_env" in attach:
        _text(attach["switch_env"], "session.attach.switch_env")
    if "clear_env" in attach:
        if not isinstance(attach["clear_env"], list) or any(not isinstance(value, str) or
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) for value in attach["clear_env"]):
            raise _error("session.attach.clear_env must contain environment variable names")
    tokens = _TOKENS | set(inputs)
    views = session.get("views")
    if views is not None:
        if bundle != "com.apple.Terminal":
            raise _error("session window views currently require the macOS Terminal bundle")
        _keys(views, {"identity", "title", "inactive_values", "modes", "inventory"}, "session.views")
        _template(views.get("identity"), tokens)
        _template(views.get("title"), tokens)
        for key in ("inactive_values", "modes"):
            if not isinstance(views.get(key), list) or not views[key] or not all(isinstance(v, str) for v in views[key]):
                raise _error(f"session.views.{key} must be a non-empty string array")
        _inventory_schema(views.get("inventory"), {"name", "owner"}, "session.views.inventory")
    operations = _keys(data.get("operations"), _CORE | _VIEWS | {"switch"}, "operations")
    required = _CORE | (_VIEWS if views is not None else set()) | ({"switch"} if attach.get("switch_env") else set())
    if required - set(operations):
        raise _error(f"missing session operations: {', '.join(sorted(required - set(operations)))}")
    for name, entry in operations.items():
        _keys(entry, {"argv"}, f"operations.{name}")
        _argv(entry.get("argv"), tokens, inputs)
    if "install" in data:
        _validate_install(data["install"])
    for key, item in inputs.items():
        if "default" in item:
            _option_value(key, item["default"], item, Path("/"), default=True)
    return ExtensionSpec(expected_id, bundle, data)


def validate_session_options(spec: ExtensionSpec, options: dict) -> None:
    session = spec.data["session"]
    rows = options[session["units_input"]] or ()
    names = [row["name"] for row in rows]
    if len(set(names)) != len(names):
        raise _error("duplicate session window name; session windows require unique names")
    if options[session["startup_command_input"]] and options[session["startup_unit_input"]] in names:
        raise _error("startup window conflicts with a configured session window")


@dataclass(frozen=True)
class SessionUnit:
    name: str
    root: Path
    command: str | None = None


class ResolvedSessionExtension:
    kind = "session"

    def __init__(self, spec: ExtensionSpec, app: AppConfig, project: ProjectConfig, commands: CommandConfig):
        self.spec, self.app, self.project = spec, app, project
        if app.match_title or app.match_document:
            raise _error("session extensions do not accept application window matchers")
        if app.launch_mode not in {"window", "instance"}:
            raise _error("launch mode must be window or instance")
        self.options = validate_options(spec, app.options, project.root)
        self.settings = spec.data["session"]
        self.bundle_id = spec.bundle_id
        self.session_name = self._option("identity_input") or f"focus-{project.name}-{app.name}"
        self.presentation = self._option("presentation_input")
        if self.presentation not in {"current", "windows", "none"}:
            raise _error("session presentation must be current, windows, or none")
        if self.presentation == "windows" and "views" not in self.settings:
            raise _error("session extension does not declare window views")
        self.deferred_close_bundle = self.bundle_id if "views" in self.settings else None
        declaration = spec.data["command"]
        executable = (getattr(commands, declaration["key"]) if declaration.get("key") else
                      shutil.which(declaration["name"]) or next((p for p in declaration.get("candidates", [])
                                                               if Path(p).is_file()), declaration["name"]))
        self.executable = executable
        self.context = {**self.options, "root": str(project.root), "project": project.name, "app": app.name,
                        "instance_dir": str(state_home() / "extensions" / spec.id / project.name / app.name),
                        "command": executable, "command_name": Path(executable).name,
                        "session": self.session_name, "shell": os.environ.get("SHELL", "/bin/zsh"),
                        "mode": app.launch_mode,
                        "title_prefix": project.name if app.legacy_slot else f"{project.name} / {app.name}"}
        self.units = tuple(SessionUnit(row["name"], Path(row["root"]), row["command"])
                           for row in self._option("units_input") or ())
        names = [unit.name for unit in self.units]
        if len(names) != len(set(names)):
            raise _error("session windows require unique names")
        if any(any(c in name for c in "\n\r\t") for name in [self.session_name, *names]):
            raise _error("session and unit names cannot contain control separators")
        self.startup_command = self._option("startup_command_input")
        self.startup_window = self._option("startup_unit_input")
        self.startup_check = self._option("startup_check_input")
        if self.startup_command and self.startup_window in names:
            raise _error("startup window conflicts with a configured session window")
        self.binding_context = hashlib.sha256(json.dumps({"spec": spec.data, "options": self.options,
            "session": self.session_name, "command": executable}, sort_keys=True).encode()).hexdigest()
        # Render every operation before any caller starts services or desktops.
        sample = dict(unit="unit", unit_root=str(project.root), unit_command="true", shell_command="exec true",
                      view="view", title="title", digest="digest")
        for name in spec.data["operations"]:
            self.operation(name, **sample)

    def _option(self, key: str):
        return self.options[self.settings[key]]

    def render(self, text: str, **values) -> str:
        context = {**self.context, **values}
        for _, token, _, _ in Formatter().parse(text):
            if token is not None and (context[token] is None or isinstance(context[token], (tuple, list, dict))):
                raise _error(f"template token {token!r} requires a scalar value; use if or spread")
        return text.format_map(context)

    def operation(self, name: str, **values) -> tuple[str, ...]:
        def arguments(entries):
            result = []
            for entry in entries:
                if isinstance(entry, str):
                    result.append(self.render(entry, **values))
                elif "spread" in entry:
                    result.extend(self.options[entry["spread"]] or ())
                elif self.options[entry["if"]]:
                    result.extend(arguments(entry["argv"]))
            return result
        argv = arguments(self.spec.data["operations"][name]["argv"])
        if not argv:
            raise _error("resolved argv cannot be empty")
        if "/" in argv[0] and not Path(argv[0]).is_absolute():
            argv[0] = str(self.project.root / argv[0])
        return tuple(argv)

    def capture(self, runner, name: str, **values):
        return runner.capture(self.operation(name, **values), cwd=self.project.root)

    def mutate(self, runner, name: str, **values) -> None:
        if runner.mutate(self.operation(name, **values), cwd=self.project.root):
            raise WorkError(f"extension {self.spec.id!r} operation {name!r} failed for {self.app.name!r}")

    def required_commands(self) -> list[tuple[str, str]]:
        sample = dict(unit="unit", unit_root=str(self.project.root), unit_command="true", shell_command="exec true",
                      view="view", title="title", digest="digest")
        names = _CORE | ({"switch"} if "switch" in self.spec.data["operations"] else set())
        if self.presentation == "windows":
            names |= _VIEWS
        required = [(f"extension {self.app.name}", self.operation(name, **sample)[0]) for name in sorted(names)]
        if any(unit.command for unit in self.units) or self.startup_command or self.startup_check:
            required.append((f"extension {self.app.name} shell", self.context["shell"]))
        if self.presentation == "windows":
            required.append(("Terminal", "/usr/bin/open" if self.app.launch_mode == "instance" else "/usr/bin/osascript"))
        return list(dict.fromkeys(required))

    def validate_paths(self) -> None:
        validate_input_paths(self.spec, self.options)
        for unit in self.units:
            if not unit.root.is_dir():
                raise WorkError(f"session window root does not exist or is not a directory: {unit.root}")

    def status(self, runner) -> bool:
        return self.capture(runner, "exists").returncode == 0

    def ready(self, runner) -> bool:
        return self.status(runner) and self.startup_status(runner) is not False

    def _rows(self, text: str, schema: dict) -> list[dict[str, str]]:
        rows = []
        for line in text.splitlines():
            parts = line.split(schema["separator"])
            if len(parts) != len(schema["columns"]):
                raise WorkError(f"extension {self.spec.id!r} returned malformed session inventory")
            rows.append(dict(zip(schema["columns"], parts)))
        return rows

    def _inventory(self, runner) -> dict[str, bool]:
        result = self.capture(runner, "list_units")
        if result.returncode:
            raise WorkError(f"extension {self.spec.id!r} could not inspect session {self.session_name!r}")
        schema = self.settings["inventory"]
        return {row["name"]: row["dead"] == schema["dead_value"] for row in self._rows(result.stdout, schema)}

    def startup_status(self, runner) -> bool | None:
        if not self.startup_command:
            return None
        if self.startup_check:
            return runner.shell_capture(self.startup_check, cwd=self.project.root).returncode == 0
        return self.status(runner) and self._inventory(runner).get(self.startup_window, True) is False

    @staticmethod
    def _unit_values(unit: SessionUnit) -> dict:
        return dict(unit=unit.name, unit_root=str(unit.root), unit_command=unit.command or "",
                    shell_command=f"exec {unit.command or ''}")

    def _start(self, runner, unit: SessionUnit, *, create: bool = True) -> None:
        values = self._unit_values(unit)
        if create:
            self.mutate(runner, "create_unit", **values)
        if unit.command:
            self.mutate(runner, "retain_unit", **values)
            self.mutate(runner, "start_unit", **values)

    def restore(self, runner, *, assume_stopped: bool = False) -> bool:
        fresh = assume_stopped or not self.status(runner)
        if fresh:
            first = self.units[0] if self.units else SessionUnit(self.settings["default_unit"], self.project.root)
            self.mutate(runner, "create", **self._unit_values(first))
            self._start(runner, first, create=False)
            existing = {first.name: False}
            print(f"ok  created {self.app.name!r} session {self.session_name!r}")
        else:
            existing = self._inventory(runner)
            print(f"ok  session {self.session_name!r} is running")
        for unit in self.units:
            if unit.name not in existing:
                self._start(runner, unit)
            elif existing[unit.name]:
                print(f"ERR session command in {unit.name!r} exited; its output is retained. "
                      "Use 'focus restart' to retry after reviewing the error.")
        if self.startup_command:
            healthy = bool(self.startup_check and runner.shell_capture(
                self.startup_check, cwd=self.project.root).returncode == 0)
            if not healthy:
                if self.startup_window not in existing:
                    self._start(runner, SessionUnit(self.startup_window, self.project.root, self.startup_command))
                elif fresh and not self.units and self.startup_window == self.settings["default_unit"]:
                    self._start(runner, SessionUnit(self.startup_window, self.project.root, self.startup_command), create=False)
                elif existing[self.startup_window]:
                    print(f"ERR startup command in {self.startup_window!r} exited; its output is retained. "
                          "Use 'focus restart' to retry after reviewing the error.")
        return fresh

    def preflight(self, runner) -> None:
        if self.presentation == "windows" and self.app.launch_mode == "window":
            from workspace_manager.terminals import TerminalWindows
            TerminalWindows(self, runner).check_access()

    def present(self, runner, *, windows=None) -> None:
        if self.presentation == "windows":
            from workspace_manager.terminals import TerminalWindows
            TerminalWindows(self, runner).open(windows=windows)

    def attach(self, runner) -> int:
        if self.presentation != "current" or runner.dry_run:
            return 0
        variable = self.settings.get("attach", {}).get("switch_env")
        return run_interactive(self.operation("switch" if variable and os.environ.get(variable) else "attach"))

    def stop_views(self, runner) -> None:
        if "views" in self.settings:
            if runner.dry_run:
                print(f"plan stop {self.app.name!r} session views")
            else:
                result = self.capture(runner, "view_list")
                if result.returncode == 0:
                    for row in self._rows(result.stdout, self.settings["views"]["inventory"]):
                        if row["owner"] == self.session_name:
                            self.mutate(runner, "view_stop", view=row["name"])
    def stop(self, runner) -> bool:
        self.stop_views(runner)
        if not self.status(runner):
            print(f"ok  session {self.session_name!r} is already stopped")
            return False
        self.mutate(runner, "stop")
        print(f"ok  stopped session {self.session_name!r}")
        return True

    def view_units(self) -> tuple[str, ...]:
        names = [unit.name for unit in self.units] or [self.settings["default_unit"]]
        if self.startup_command and self.startup_window not in names:
            names.append(self.startup_window)
        return tuple(names)

    def view_identity(self, unit: str) -> str:
        return self.render(self.settings["views"]["identity"], unit=unit,
                           digest=hashlib.sha256(unit.encode()).hexdigest()[:12])

    def view_title(self, unit: str) -> str:
        return self.render(self.settings["views"]["title"], unit=unit)

    def view_attached(self, runner, view: str) -> bool:
        result = self.capture(runner, "view_attached", view=view)
        return result.returncode == 0 and result.stdout.strip() not in self.settings["views"]["inactive_values"]

    def view_slot(self, unit: str) -> str:
        return f"terminal:{unit}" if self.app.legacy_slot else f"terminal:{self.app.name}:{unit}"

    def view_plan(self, runner, unit: str) -> tuple[str, bool, bool]:
        """Return view identity, whether it exists, and whether its client is reusable."""
        view = self.view_identity(unit)
        exists = self.capture(runner, "view_exists", view=view).returncode == 0
        if exists:
            owner = self.capture(runner, "view_owner", view=view)
            if owner.returncode or owner.stdout.strip() != self.session_name:
                raise WorkError(f"Refusing to reuse unrelated session {view!r}")
        if exists and self.view_attached(runner, view):
            mode = self.capture(runner, "view_mode", view=view)
            if self.app.reuse_existing and mode.returncode == 0 and mode.stdout.strip() in self.settings["views"]["modes"]:
                return view, True, True
            if self.app.reuse_existing:
                self.mutate(runner, "view_detach", view=view)
            else:
                view += "-" + uuid.uuid4().hex[:12]
                exists = False
        return view, exists, False

    def prepare_view(self, runner, unit: str, view: str, exists: bool) -> None:
        if not exists:
            self.mutate(runner, "view_create", view=view)
            self.mutate(runner, "view_set_owner", view=view)
        self.mutate(runner, "view_select", view=view, unit=unit)

    def confirm_view(self, runner, view: str, unit: str) -> None:
        deadline = time.monotonic() + 10
        while not self.view_attached(runner, view):
            if time.monotonic() >= deadline:
                raise WorkError(f"Terminal did not attach to {unit!r}; inspect its window before retrying")
            time.sleep(0.1)
        self.mutate(runner, "view_set_mode", view=view)
