"""Declarative application extensions; specifications contain no executable Python."""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import hashlib
from importlib import resources
import json
from pathlib import Path
import re
import shutil
from string import Formatter
import tomllib
from urllib.parse import unquote, urlparse

from workspace_manager.errors import WorkError
from workspace_manager.models import AppConfig, CommandConfig, ProjectConfig
from workspace_manager.paths import config_home, state_home


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_CONTEXT = {"root", "project", "instance_dir", "command", "command_name"}
_TYPES = {"string", "path", "strings", "paths", "boolean"}


@dataclass(frozen=True)
class ExtensionSpec:
    id: str
    bundle_id: str
    data: dict


def _error(message: str) -> WorkError:
    return WorkError(f"extension specification: {message}")


def _keys(value, allowed: set[str], where: str) -> dict:
    if not isinstance(value, dict):
        raise _error(f"{where} must be a table")
    unknown = set(value) - allowed
    if unknown:
        raise _error(f"{where}: unknown field(s): {', '.join(sorted(unknown))}")
    return value


def _text(value, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise _error(f"{where} must be a non-empty string")
    return value


def _bools(data: dict, names: tuple[str, ...], where: str) -> None:
    for name in names:
        if name in data and type(data[name]) is not bool:
            raise _error(f"{where}.{name} must be true or false")


def _template(value: str, tokens: set[str]) -> None:
    _text(value, "template")
    try:
        for _, token, format_spec, conversion in Formatter().parse(value):
            if token is not None and (token not in tokens or format_spec or conversion):
                raise _error(f"unknown or unsupported template token: {token!r}")
    except ValueError as exc:
        raise _error(f"invalid template: {value!r}") from exc


def _argv(value, tokens: set[str], inputs: dict, *, depth: int = 0) -> None:
    if depth > 8 or not isinstance(value, list) or not value:
        raise _error("argv must be a non-empty array with bounded argument groups")
    for part in value:
        if isinstance(part, str):
            _template(part, tokens)
        elif isinstance(part, dict) and set(part) == {"spread"}:
            key = part["spread"]
            if not isinstance(key, str) or key not in inputs or inputs[key]["type"] not in {"strings", "paths"}:
                raise _error("spread must name a strings or paths input")
        elif isinstance(part, dict) and set(part) == {"if", "argv"}:
            if not isinstance(part["if"], str) or part["if"] not in inputs:
                raise _error("if must name an input")
            _argv(part["argv"], tokens, inputs, depth=depth + 1)
        else:
            raise _error("argv entries must be strings, {spread=...}, or {if=..., argv=[...]}")


def _validate_spec(data: dict, expected_id: str) -> ExtensionSpec:
    _keys(data, {"version", "id", "bundle_id", "command", "inputs", "launch", "status", "match",
                 "detached", "adopt_single_on_desktop", "require_match", "restore_on_fresh_boot",
                 "reopen_on_restore", "reopen_on_restore_input"}, "root")
    if type(data.get("version")) is not int or data["version"] != 1:
        raise _error("version must be 1")
    if data.get("id") != expected_id:
        raise _error(f"id must match filename: {expected_id!r}")
    bundle = _text(data.get("bundle_id"), "bundle_id")
    _bools(data, ("detached", "adopt_single_on_desktop", "require_match", "restore_on_fresh_boot",
                  "reopen_on_restore"), "root")
    inputs = data.get("inputs", {})
    if not isinstance(inputs, dict):
        raise _error("inputs must be a table")
    for name, item in inputs.items():
        if not _NAME.fullmatch(name) or name in _CONTEXT:
            raise _error(f"invalid or reserved input name: {name!r}")
        _keys(item, {"type", "default", "required", "must_exist", "path_kind", "schemes"}, f"inputs.{name}")
        if not isinstance(item.get("type"), str) or item["type"] not in _TYPES:
            raise _error(f"inputs.{name}.type must be one of {', '.join(sorted(_TYPES))}")
        _bools(item, ("required", "must_exist"), f"inputs.{name}")
        if item.get("must_exist") and item["type"] not in {"path", "paths"}:
            raise _error(f"inputs.{name}.must_exist requires a path input")
        if "path_kind" in item and (item["type"] not in {"path", "paths"}
                or not isinstance(item["path_kind"], str) or item["path_kind"] not in {"any", "file", "directory"}):
            raise _error(f"inputs.{name}.path_kind must be any, file, or directory for a path input")
        if "schemes" in item:
            if item["type"] not in {"string", "strings"} or not isinstance(item["schemes"], list) or not item["schemes"]:
                raise _error(f"inputs.{name}.schemes requires a string input and non-empty array")
            for scheme in item["schemes"]:
                _text(scheme, f"inputs.{name}.schemes")
        if "default" in item:
            values = item["default"] if isinstance(item["default"], list) else [item["default"]]
            for value in values:
                if isinstance(value, str):
                    _template(value, {"root"})
    tokens = _CONTEXT | set(inputs)
    command = _keys(data.get("command", {}), {"key", "name", "candidates"}, "command")
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
    launch = _keys(data.get("launch"), {"window", "instance"}, "launch")
    for mode in ("window", "instance"):
        entry = _keys(launch.get(mode), {"argv"}, f"launch.{mode}")
        _argv(entry.get("argv"), tokens, inputs)
    status = _keys(data.get("status", {}), {"window", "instance"}, "status")
    for mode, entry in status.items():
        _keys(entry, {"argv", "any_contains", "line_all", "ignore_case"}, f"status.{mode}")
        _argv(entry.get("argv"), tokens, inputs)
        _bools(entry, ("ignore_case",), f"status.{mode}")
        predicates = set(entry) & {"any_contains", "line_all"}
        if len(predicates) != 1:
            raise _error(f"status.{mode} requires exactly one of any_contains or line_all")
        values = entry[next(iter(predicates))]
        if not isinstance(values, list) or not values:
            raise _error(f"status.{mode} predicate must be a non-empty array")
        for value in values:
            _template(value, tokens)
    match = _keys(data.get("match", {}), {"kind", "input", "file_suffix", "title_suffix", "title_separator",
                "folders_key", "folder_path_key", "jsonc", "title", "document"}, "match")
    kind = match.get("kind", "none")
    if not isinstance(kind, str) or kind not in {"none", "workspace", "document_values", "glob"}:
        raise _error("match.kind must be none, workspace, document_values, or glob")
    fields = {
        "none": {"kind"},
        "workspace": {"kind", "input", "file_suffix", "title_suffix", "title_separator", "folders_key", "folder_path_key", "jsonc"},
        "document_values": {"kind", "input"},
        "glob": {"kind", "title", "document"},
    }
    _keys(match, fields[kind], "match")
    if kind in {"workspace", "document_values"}:
        key = match.get("input")
        needed = "path" if kind == "workspace" else "strings"
        if not isinstance(key, str) or key not in inputs or inputs[key]["type"] != needed:
            raise _error(f"match.input must name a {needed} input")
        if kind == "workspace" and not (inputs[key].get("required") or "default" in inputs[key]):
            raise _error("workspace match input must be required or have a default")
    if kind == "workspace":
        _bools(match, ("jsonc",), "match")
        for key in ("file_suffix", "title_suffix", "title_separator", "folders_key", "folder_path_key"):
            _text(match.get(key), f"match.{key}")
    if kind == "glob":
        if not (match.get("title") or match.get("document")):
            raise _error("glob matcher requires title or document")
        for key in ("title", "document"):
            if key in match:
                _template(match[key], tokens)
                if not re.search(r"[^\s*?\[\]!]", match[key]):
                    raise _error("glob matcher must identify a project, not only wildcards")
    if "reopen_on_restore_input" in data:
        key = data["reopen_on_restore_input"]
        if not isinstance(key, str) or key not in inputs or inputs[key]["type"] != "boolean":
            raise _error("reopen_on_restore_input must name a boolean input")
    spec = ExtensionSpec(expected_id, bundle, data)
    # Validate default types without requiring optional/required user values yet.
    for key, item in inputs.items():
        if "default" in item:
            _option_value(key, item["default"], item, Path("/"), default=True)
    return spec


def load_extension(extension_id: str) -> ExtensionSpec:
    if not isinstance(extension_id, str) or not _NAME.fullmatch(extension_id):
        raise _error("extension id must contain only letters, numbers, '-' and '_'")
    bundled = resources.files("workspace_manager").joinpath("extension_specs", f"{extension_id}.toml")
    local = config_home() / "extensions" / f"{extension_id}.toml"
    if bundled.is_file() and local.exists():
        raise _error(f"user extension {extension_id!r} collides with a bundled extension; choose a different id")
    source = bundled if bundled.is_file() else local
    try:
        data = tomllib.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise _error(f"unknown extension {extension_id!r}; add {local}") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise _error(f"cannot read {extension_id!r}: {exc}") from exc
    return _validate_spec(data, extension_id)


def _option_value(key: str, value, item: dict, root: Path, *, default: bool = False):
    kind = item["type"]
    if kind == "boolean":
        if type(value) is not bool:
            raise _error(f"option {key!r} must be true or false")
        return value
    values = value if kind in {"strings", "paths"} else [value]
    if not isinstance(values, (list, tuple)) or (kind in {"strings", "paths"} and isinstance(value, str)):
        raise _error(f"option {key!r} must be an array of strings")
    result = []
    for part in values:
        part = _text(part, f"option {key!r}")
        if default:
            part = part.format(root=str(root))
        if kind in {"path", "paths"}:
            path = Path(part).expanduser()
            part = str((root / path).resolve() if not path.is_absolute() else path.resolve())
        if "schemes" in item:
            parsed = urlparse(part)
            if parsed.scheme not in item["schemes"] or not parsed.netloc:
                raise _error(f"option {key!r} requires URL schemes: {', '.join(item['schemes'])}")
        result.append(part)
    return tuple(result) if kind in {"strings", "paths"} else result[0]


def validate_options(spec: ExtensionSpec, options: dict, root: Path) -> dict:
    if not isinstance(options, dict):
        raise _error("app.options must be a table")
    inputs = spec.data.get("inputs", {})
    unknown = set(options) - set(inputs)
    if unknown:
        raise _error(f"{spec.id}: unknown option(s): {', '.join(sorted(unknown))}")
    result = {}
    for key, item in inputs.items():
        if key in options:
            # Already-normalized optional absence is allowed for repeated resolution.
            if options[key] is None and not item.get("required") and "default" not in item:
                result[key] = None
            else:
                result[key] = _option_value(key, options[key], item, root)
        elif "default" in item:
            result[key] = _option_value(key, item["default"], item, root, default=True)
        elif item.get("required"):
            raise _error(f"{spec.id}: required option {key!r} is missing")
        else:
            result[key] = None
    return result


class ResolvedExtension:
    def __init__(self, spec: ExtensionSpec, app: AppConfig, project: ProjectConfig, commands: CommandConfig):
        self.spec, self.app, self.project = spec, app, project
        self.options = validate_options(spec, app.options, project.root)
        self.bundle_id = spec.bundle_id
        namespace = getattr(app, "legacy_slot", None)
        directory = (state_home() / namespace / project.name if namespace else
                     state_home() / "extensions" / spec.id / project.name / app.name)
        declaration = spec.data["command"]
        if declaration.get("key"):
            executable = getattr(commands, declaration["key"])
        else:
            executable = shutil.which(declaration["name"]) or next(
                (part for part in declaration.get("candidates", []) if Path(part).is_file()), declaration["name"])
        self.context = {**self.options, "root": str(project.root), "project": project.name,
                        "instance_dir": str(directory), "command": executable, "command_name": Path(executable).name}
        self.detached = spec.data.get("detached", False)
        self.adopt_single_on_desktop = spec.data.get("adopt_single_on_desktop", False)
        self.require_match = spec.data.get("require_match", False)
        match = spec.data.get("match", {})
        self.has_match = (match.get("kind", "none") != "none" and
                          (match.get("kind") != "document_values" or bool(self.options[match["input"]])))
        self.restore_on_fresh_boot = spec.data.get("restore_on_fresh_boot", False)
        self.reopen_on_restore = self.options.get(spec.data.get("reopen_on_restore_input"),
                                                   spec.data.get("reopen_on_restore", True))
        data = {"spec": spec.data, "options": self.options, "context": self.context, "mode": app.launch_mode}
        self.binding_context = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        # Resolve the selected mode and matching rules before the caller starts
        # services or opens a desktop. Optional values need explicit if groups.
        self.command(app.launch_mode)
        self.status_command(app.launch_mode)
        status = spec.data.get("status", {}).get(app.launch_mode, {})
        for value in status.get("any_contains", status.get("line_all", [])):
            self._render(value)
        match = spec.data.get("match", {})
        if match.get("kind") == "glob":
            for key in ("title", "document"):
                if key in match:
                    pattern = self._render(match[key])
                    if not re.search(r"[^\s*?\[\]!]", pattern):
                        raise _error("resolved glob matcher must identify a project, not only wildcards")

    def _render(self, value: str) -> str:
        for _, token, _, _ in Formatter().parse(value):
            if token is not None and (self.context[token] is None or isinstance(self.context[token], (list, tuple))):
                raise _error(f"template token {token!r} requires a scalar value; use if or spread")
        return value.format_map(self.context)

    def _arguments(self, entries: list) -> tuple[str, ...]:
        args: list[str] = []
        for entry in entries:
            if isinstance(entry, str):
                args.append(self._render(entry))
            elif "spread" in entry:
                args.extend(self.options[entry["spread"]] or ())
            elif self.options[entry["if"]]:
                args.extend(self._arguments(entry["argv"]))
        if not args:
            raise _error("resolved argv cannot be empty")
        return tuple(args)

    def _command(self, entries: list) -> tuple[str, ...]:
        args = self._arguments(entries)
        first = args[0]
        if "/" in first and not Path(first).is_absolute():
            first = str(self.project.root / first)
        return (first, *args[1:])

    def command(self, mode: str) -> tuple[str, ...]:
        if mode not in {"window", "instance"}:
            raise _error("launch mode must be window or instance")
        return self._command(self.spec.data["launch"][mode]["argv"])

    def has_status(self, mode: str) -> bool:
        return mode in self.spec.data.get("status", {})

    def status_command(self, mode: str) -> tuple[str, ...]:
        entry = self.spec.data.get("status", {}).get(mode)
        return self._command(entry["argv"]) if entry else ()

    def running(self, runner, mode: str) -> bool | None:
        entry = self.spec.data.get("status", {}).get(mode)
        if entry is None:
            return None
        result = runner.capture(self.status_command(mode), cwd=self.project.root)
        if result.returncode:
            return False
        output = result.stdout + result.stderr
        predicate = "line_all" if "line_all" in entry else "any_contains"
        values = [self._render(value) for value in entry[predicate]]
        if entry.get("ignore_case", False):
            output, values = output.casefold(), [value.casefold() for value in values]
        return (any(all(value in line for value in values) for line in output.splitlines())
                if predicate == "line_all" else any(value in output for value in values))

    def validate_paths(self) -> None:
        for key, item in self.spec.data.get("inputs", {}).items():
            if item.get("must_exist") and self.options[key] is not None:
                values = self.options[key] if item["type"] == "paths" else (self.options[key],)
                for path in values:
                    if not Path(path).exists():
                        raise WorkError(f"extension {self.spec.id!r} {key} path does not exist: {path}")
                    kind = item.get("path_kind", "any")
                    if (kind == "file" and not Path(path).is_file()) or (kind == "directory" and not Path(path).is_dir()):
                        raise WorkError(f"extension {self.spec.id!r} {key} path must be a {kind}: {path}")

    def _folder_roots(self, workspace: Path, match: dict) -> list[Path]:
        roots = [self.project.root.resolve()]
        if workspace.suffix != match["file_suffix"] or not workspace.is_file():
            return roots
        try:
            text = workspace.read_text(encoding="utf-8-sig")
            if match.get("jsonc", False):
                string = r'"(?:\\.|[^"\\])*"'
                text = re.sub(string + r'|//[^\r\n]*|/\*[\s\S]*?\*/',
                              lambda found: found[0] if found[0].startswith('"') else ' ', text)
                text = re.sub(string + r'|,(?=\s*[}\]])',
                              lambda found: found[0] if found[0].startswith('"') else '', text)
            data = json.loads(text)
            for folder in data.get(match["folders_key"], []):
                if isinstance(folder, dict) and isinstance(folder.get(match["folder_path_key"]), str):
                    roots.append((workspace.parent / folder[match["folder_path_key"]]).resolve())
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            raise WorkError(f"Cannot read extension workspace folders: {exc}") from exc
        return roots

    def matches(self, window: dict) -> bool:
        match = self.spec.data.get("match", {})
        kind = match.get("kind", "none")
        if kind == "none":
            return False
        if kind == "document_values":
            return bool(window["document"] and window["document"] in (self.options[match["input"]] or ()))
        if kind == "workspace":
            workspace = Path(self.options[match["input"]])
            title = workspace.stem + match["title_suffix"] if workspace.suffix == match["file_suffix"] else workspace.name
            separator = re.escape(match["title_separator"])
            if not re.search(r"(?:^|" + separator + ")" + re.escape(title) + r"(?:" + separator + "|$)", window["title"]):
                return False
            document = window["document"]
            if document.startswith("file:"):
                path = Path(unquote(urlparse(document).path)).resolve()
                return path == workspace.resolve() or any(path.is_relative_to(root) for root in self._folder_roots(workspace, match))
            return True
        document = window["document"]
        if document.startswith("file:"):
            document = str(Path(unquote(urlparse(document).path)).resolve())
        for key, value in (("title", window["title"]), ("document", document)):
            if key in match:
                pattern = self._render(match[key])
                if key == "document" and pattern.startswith("/"):
                    pattern = str(Path(pattern).resolve())
                if not fnmatchcase(value, pattern):
                    return False
        return True


def resolve_extension(app: AppConfig, project: ProjectConfig, commands: CommandConfig) -> ResolvedExtension:
    return ResolvedExtension(load_extension(app.extension), app, project, commands)
