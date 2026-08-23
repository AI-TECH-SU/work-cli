#!/usr/bin/env python3
"""Render the release Homebrew formula from explicit, auditable inputs."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = ROOT / "packaging" / "homebrew" / "work.rb.in"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?$")


def web_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise argparse.ArgumentTypeError("must be an http or https URL")
    return value


def sha256(value: str) -> str:
    normalized = value.casefold()
    if not SHA256.fullmatch(normalized):
        raise argparse.ArgumentTypeError("must contain exactly 64 hexadecimal characters")
    return normalized


def version(value: str) -> str:
    if not VERSION.fullmatch(value):
        raise argparse.ArgumentTypeError("must be a release version such as 0.1.0")
    return value


def render_formula(
    template: str,
    *,
    homepage: str,
    url: str,
    archive_sha256: str,
    release_version: str,
) -> str:
    replacements = {
        "@HOMEPAGE@": homepage,
        "@URL@": url,
        "@SHA256@": archive_sha256,
        "@VERSION@": release_version,
    }
    rendered = template
    for marker, value in replacements.items():
        rendered = rendered.replace(marker, value)
    remaining = sorted(set(re.findall(r"@[A-Z0-9_]+@", rendered)))
    if remaining:
        raise ValueError(f"unresolved formula markers: {', '.join(remaining)}")
    return rendered


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--homepage", required=True, type=web_url)
    parser.add_argument("--url", required=True, type=web_url, help="release source archive URL")
    parser.add_argument("--sha256", required=True, type=sha256, dest="archive_sha256")
    parser.add_argument("--version", required=True, type=version, dest="release_version")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    template = args.template.read_text(encoding="utf-8")
    rendered = render_formula(
        template,
        homepage=args.homepage,
        url=args.url,
        archive_sha256=args.archive_sha256,
        release_version=args.release_version,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
