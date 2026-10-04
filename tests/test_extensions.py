from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from workspace_manager.errors import WorkError
from workspace_manager.extensions import load_extension, resolve_extension, validate_options, _validate_spec
from workspace_manager.models import AppConfig, ChromeConfig, CommandConfig, ContextConfig, ProjectConfig, StartupConfig, TmuxConfig, VSCodeConfig


CUSTOM = '''version = 1
id = "notes"
bundle_id = "com.example.Notes"
require_match = true
[command]
name = "./bin/notes"
[inputs.file]
type = "path"
required = true
must_exist = true
[inputs.title]
type = "string"
default = "Project notes"
[inputs.flags]
type = "strings"
default = []
[launch.window]
argv = ["{command}", "--new-window", {spread = "flags"}, "{file}"]
[launch.instance]
argv = ["{command}", "--data={instance_dir}", "{file}"]
[match]
kind = "glob"
title = "{title}*"
document = "{file}"
'''


class ProbeRunner:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.calls = []

    def capture(self, command, *, cwd=None):
        self.calls.append((tuple(command), cwd))
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, self.stderr)


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config"),
                                           "XDG_STATE_HOME": str(self.root / "state")})
        self.env.start()
        self.workspace = self.root / "Demo.code-workspace"
        self.workspace.write_text("{}")
        self.context = self.root / "PROJECT.md"
        self.context.write_text("# Project")
        self.project = ProjectConfig("demo", self.root, TmuxConfig("demo"), VSCodeConfig(False),
                                     ChromeConfig(False), ContextConfig(), StartupConfig(), self.root / "demo.toml")
        self.commands = CommandConfig("tmux", "code", "/Applications/Google Chrome")

    def tearDown(self):
        self.env.stop()
        self.temporary.cleanup()

    def extension(self, extension_id, *, options=None, name="resource", mode="window", legacy=None):
        return resolve_extension(AppConfig(name, extension=extension_id, options=options or {},
                                           launch_mode=mode, legacy_slot=legacy), self.project, self.commands)

    def write_custom(self, source=CUSTOM, extension_id="notes"):
        directory = self.root / "config/focus/extensions"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{extension_id}.toml"
        path.write_text(source)
        return path

    def test_bundled_editor_launch_preserves_profile_context_and_legacy_instance_path(self):
        extension = self.extension("vscode", options={"workspace": str(self.workspace), "profile": "Demo Profile",
                                                      "context": str(self.context)}, legacy="vscode")
        self.assertEqual(extension.command("window"), ("code", "--new-window", "--profile", "Demo Profile",
                                                       str(self.workspace), str(self.context)))
        self.assertEqual(extension.command("instance")[1:3],
                         ("--user-data-dir", str(self.root / "state/focus/vscode/demo")))
        self.assertFalse(extension.detached)
        extension.validate_paths()

    def test_bundled_browser_launch_preserves_flags_and_process_behavior(self):
        extension = self.extension("chrome", options={"profile": "Profile 2", "urls": ["https://example.test/a"]},
                                   legacy="chrome")
        self.assertEqual(extension.command("window"), (self.commands.chrome, "--profile-directory=Profile 2",
                                                       "--new-window", "https://example.test/a"))
        self.assertEqual(extension.command("instance")[1],
                         f"--user-data-dir={self.root / 'state/focus/chrome/demo'}")
        self.assertTrue(extension.detached)
        self.assertTrue(extension.adopt_single_on_desktop)
        self.assertTrue(extension.restore_on_fresh_boot)

    def test_explicit_resources_get_distinct_instance_directories(self):
        first = self.extension("chrome", name="browser-one")
        second = self.extension("chrome", name="browser-two")
        self.assertNotEqual(first.command("instance"), second.command("instance"))
        self.assertIn("/extensions/chrome/demo/browser-one", first.command("instance")[1])

    def test_builtin_optional_inputs_do_not_add_empty_arguments(self):
        self.assertEqual(self.extension("vscode").command("window"), ("code", "--new-window", str(self.root)))
        self.assertEqual(self.extension("chrome").command("window"),
                         (self.commands.chrome, "--profile-directory=Default", "--new-window"))

    def test_external_extension_needs_no_python_handler_and_keeps_argv_literal(self):
        self.write_custom()
        extension = self.extension("notes", options={"file": "PROJECT.md", "flags": ["$(not-a-shell)", "a b"]})
        self.assertEqual(extension.command("window"),
                         (str(self.root / "bin/notes"), "--new-window", "$(not-a-shell)", "a b", str(self.context)))
        self.assertFalse(extension.has_status("window"))
        runner = ProbeRunner()
        self.assertIsNone(extension.running(runner, "window"))
        self.assertEqual(runner.calls, [])
        self.assertTrue(extension.matches({"title": "Project notes — Notes", "document": self.context.as_uri()}))
        self.assertFalse(extension.matches({"title": "Other", "document": self.context.as_uri()}))
        self.assertTrue(extension.require_match)

    def test_options_are_typed_resolved_and_unknown_keys_rejected(self):
        spec = load_extension("vscode")
        values = validate_options(spec, {"workspace": ".", "context": "PROJECT.md"}, self.root)
        self.assertEqual(values["workspace"], str(self.root))
        self.assertEqual(values["context"], str(self.context))
        for options in ({"unknown": True}, {"workspace": []}, {"profile": True}, {"profile": ""}):
            with self.subTest(options=options), self.assertRaises(WorkError):
                validate_options(spec, options, self.root)
        for options in ({"urls": "https://example.test"}, {"urls": [True]}, {"urls": ["ftp://example.test"]},
                        {"reopen_on_restore": "false"}):
            with self.subTest(options=options), self.assertRaises(WorkError):
                validate_options(load_extension("chrome"), options, self.root)

    def test_required_option_fails_before_launch(self):
        self.write_custom()
        with self.assertRaisesRegex(WorkError, "required option 'file'"):
            self.extension("notes")

    def test_missing_paths_are_checked_separately_without_filesystem_mutation(self):
        extension = self.extension("vscode", options={"workspace": "missing.code-workspace"})
        with self.assertRaisesRegex(WorkError, "path does not exist"):
            extension.validate_paths()
        self.assertFalse((self.root / "state").exists())
        extension = self.extension("vscode", options={"context": str(self.root)})
        with self.assertRaisesRegex(WorkError, "must be a file"):
            extension.validate_paths()

    def test_user_spec_cannot_shadow_bundled_spec_or_escape_directory(self):
        self.write_custom(CUSTOM, "vscode")
        with self.assertRaisesRegex(WorkError, "collides"):
            load_extension("vscode")
        for identifier in ("../notes", "not known", "missing", ""):
            with self.subTest(identifier=identifier), self.assertRaises(WorkError):
                load_extension(identifier)

    def test_unknown_fields_version_and_identity_are_rejected(self):
        base = load_extension("chrome").data
        variants = [dict(base, version=2), dict(base, version=True), dict(base, id="other"),
                    dict(base, handler="chrome"), dict(base, launch={"window": {"argv": ["chrome"]}}),
                    dict(base, detached="true"), dict(base, bundle_id="")]
        for data in variants:
            with self.subTest(data=data), self.assertRaises(WorkError):
                _validate_spec(data, "chrome")

    def test_invalid_argument_templates_groups_and_unknown_inputs_are_rejected(self):
        for argv in (["{unknown}"], ["{root.name}"], ["{root!r}"], ["{root:>9}"], ["{}"], ["{root"], [],
                     [{"spread": "profile"}], [{"if": "unknown", "argv": ["x"]}], [{"shell": "code"}], [True]):
            data = deepcopy(load_extension("chrome").data)
            data["launch"]["window"]["argv"] = argv
            with self.subTest(argv=argv), self.assertRaises(WorkError):
                _validate_spec(data, "chrome")

    def test_optional_scalar_requires_if_group_and_arrays_require_spread(self):
        data = deepcopy(load_extension("vscode").data)
        data["launch"]["window"]["argv"] = ["{command}", "{profile}"]
        with patch("workspace_manager.extensions.load_extension", return_value=_validate_spec(data, "vscode")):
            with self.assertRaisesRegex(WorkError, "requires a scalar"):
                self.extension("vscode").command("window")
        data = deepcopy(load_extension("chrome").data)
        data["launch"]["window"]["argv"] = ["{command}", "{urls}"]
        with patch("workspace_manager.extensions.load_extension", return_value=_validate_spec(data, "chrome")):
            with self.assertRaisesRegex(WorkError, "requires a scalar"):
                self.extension("chrome").command("window")

    def test_probe_predicates_require_one_explicit_detection_rule(self):
        data = deepcopy(load_extension("chrome").data)
        data["status"]["window"]["any_contains"] = ["anything"]
        with self.assertRaisesRegex(WorkError, "exactly one"):
            _validate_spec(data, "chrome")

    def test_match_templates_fail_before_any_window_inspection_or_launch(self):
        self.write_custom(CUSTOM.replace('default = "Project notes"', ''))
        with self.assertRaisesRegex(WorkError, "requires a scalar"):
            self.extension("notes", options={"file": "PROJECT.md"})
        self.write_custom()
        with self.assertRaisesRegex(WorkError, "not only wildcards"):
            self.extension("notes", options={"file": "PROJECT.md", "title": "*"})

    def test_invalid_nested_spec_types_fail_as_configuration_errors(self):
        for malformed in ([], {}, ["invalid"], {"invalid": "value"}):
            variants = (("command", {"key": malformed}),
                        ("inputs", {"bad": {"type": malformed}}),
                        ("inputs", {"bad": {"type": "path", "path_kind": malformed}}),
                        ("match", {"kind": malformed}),
                        ("match", {"kind": "document_values", "input": malformed}),
                        ("reopen_on_restore_input", malformed))
            for key, value in variants:
                data = deepcopy(load_extension("chrome").data)
                data[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(WorkError):
                    _validate_spec(data, "chrome")

    def test_editor_status_probe_matches_workspace_and_case_insensitively(self):
        extension = self.extension("vscode", options={"workspace": str(self.workspace)})
        runner = ProbeRunner(str(self.workspace).upper())
        self.assertTrue(extension.running(runner, "window"))
        self.assertEqual(runner.calls[0][0], ("code", "--status"))
        self.assertFalse(extension.running(ProbeRunner(str(self.workspace), returncode=1), "window"))

    def test_browser_status_requires_same_process_line_and_selected_instance(self):
        extension = self.extension("chrome", options={"profile": "Profile 2"})
        self.assertFalse(extension.running(ProbeRunner("Google Chrome\nOther --profile-directory=Profile 2"), "window"))
        self.assertTrue(extension.running(ProbeRunner("Google Chrome --profile-directory=Profile 2"), "window"))
        self.assertFalse(extension.running(ProbeRunner("Google Chrome --profile-directory=Profile 2"), "instance"))
        self.assertTrue(extension.running(ProbeRunner(" ".join(extension.command("instance"))), "instance"))

    def test_workspace_matching_keeps_jsonc_and_resolved_symlink_folders(self):
        folder = self.root / "backend"
        folder.mkdir()
        (self.root / "linked").symlink_to(folder)
        self.workspace.write_text('''{
            // Comments and trailing commas are accepted.
            "folders": [{"path": "linked",},],
            "settings": {"value": "https://example.test/a,}/*literal*/"},
        }''')
        extension = self.extension("vscode", options={"workspace": str(self.workspace)})
        row = {"title": "file.php — Demo (Workspace) — Visual Studio Code", "document": (folder / "file.php").as_uri()}
        self.assertTrue(extension.matches(row))
        self.assertFalse(extension.matches({**row, "document": "file:///other/repo/file.php"}))
        self.assertFalse(extension.matches({**row, "title": "Other Demo (Workspace)"}))
        self.assertTrue(extension.matches({**row, "document": ""}))

    def test_browser_match_uses_document_identity_only(self):
        extension = self.extension("chrome", options={"urls": ["https://example.test/project"]})
        self.assertTrue(extension.has_match)
        self.assertTrue(extension.matches({"document": "https://example.test/project", "title": "Anything"}))
        self.assertFalse(extension.matches({"document": "", "title": "https://example.test/project"}))
        self.assertFalse(extension.matches({"document": "https://example.test/other", "title": "Same project"}))

    def test_empty_document_values_offer_no_semantic_matcher(self):
        extension = self.extension("chrome")
        self.assertFalse(extension.has_match)
        self.assertFalse(extension.matches({"document": "https://example.test/project", "title": "Project"}))

    def test_binding_fingerprint_changes_with_spec_options_root_mode_and_command(self):
        self.write_custom()
        options = {"file": "PROJECT.md"}
        original = self.extension("notes", options=options).binding_context
        self.write_custom(CUSTOM.replace('"--new-window"', '"--new-project"'))
        self.assertNotEqual(original, self.extension("notes", options=options).binding_context)
        self.write_custom()
        self.assertNotEqual(original, self.extension("notes", options={**options, "title": "Different"}).binding_context)
        self.assertNotEqual(original, self.extension("notes", options=options, mode="instance").binding_context)
        app = AppConfig("resource", extension="notes", options=options)
        self.assertNotEqual(original, resolve_extension(app, replace(self.project, root=self.root / "other"), self.commands).binding_context)
        code = AppConfig("editor", extension="vscode")
        self.assertNotEqual(resolve_extension(code, self.project, self.commands).binding_context,
                            resolve_extension(code, self.project, replace(self.commands, code="other-code")).binding_context)


if __name__ == "__main__":
    unittest.main()
