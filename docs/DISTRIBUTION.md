# Distribution

## Dependency policy

tmux is a required system dependency and is distributed through Homebrew. It is
not copied into the Python wheel or vendored as a private binary.

This keeps ownership transparent:

- Homebrew installs and upgrades tmux.
- The Python package installs and upgrades `work`.
- `work doctor` validates both.

## Homebrew release

The formula template is
`packaging/homebrew/work.rb.in`. Its authoritative dependency declaration is:

```ruby
depends_on "tmux"
```

After publishing a source archive, calculate its SHA-256 digest and render the
formula with explicit release inputs:

```console
shasum -a 256 dist/local-workspace-manager-0.1.0.tar.gz
python3 scripts/render_homebrew_formula.py \
  --homepage https://github.com/AI-TECH-SU/work-cli \
  --url https://github.com/AI-TECH-SU/work-cli/archive/refs/tags/v0.1.0.tar.gz \
  --sha256 THE_64_CHARACTER_DIGEST \
  --version 0.1.0 \
  --output ../homebrew-tap/Formula/work.rb
```

The release URL and digest are deliberately not guessed in this repository.
They become known only after a remote and immutable release artifact exist.
The rendered formula installs Python, tmux, and `work`, so the end-user command
is simply:

```console
brew install AI-TECH-SU/tap/work
```

## pipx or source installation

Python package managers cannot install a macOS system executable. A user who
installs `work` through pipx or from source can repair the missing dependency
through the CLI:

```console
work doctor --fix
```

The command shows the exact `brew install tmux` operation and asks for approval
before running it. Non-interactive provisioning must opt in explicitly:

```console
work doctor --fix --yes
```

If Homebrew is unavailable, `work` reports that boundary and points to
`https://brew.sh`; it does not download or execute an installer itself.
