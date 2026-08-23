from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_homebrew_formula.py"
SPEC = importlib.util.spec_from_file_location("render_homebrew_formula", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DistributionTests(unittest.TestCase):
    def test_formula_declares_tmux_and_renders_release_values(self) -> None:
        template = (ROOT / "packaging" / "homebrew" / "work.rb.in").read_text(encoding="utf-8")

        rendered = MODULE.render_formula(
            template,
            homepage="https://example.com/work",
            url="https://example.com/work-0.1.0.tar.gz",
            archive_sha256="a" * 64,
            release_version="0.1.0",
        )

        self.assertIn('depends_on "tmux"', rendered)
        self.assertIn('depends_on "python@3.13"', rendered)
        self.assertIn("depends_on :macos", rendered)
        self.assertIn('sha256 "' + "a" * 64 + '"', rendered)
        self.assertNotRegex(rendered, r"@[A-Z0-9_]+@")

    def test_renderer_writes_formula(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Formula" / "work.rb"
            code = MODULE.main(
                [
                    "--homepage",
                    "https://example.com/work",
                    "--url",
                    "https://example.com/work-0.1.0.tar.gz",
                    "--sha256",
                    "b" * 64,
                    "--version",
                    "0.1.0",
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
