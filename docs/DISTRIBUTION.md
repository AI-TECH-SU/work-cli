# Distribution

For the current development installation, use the [setup guide](SETUP.md).
The local tap currently has no published Focus formula. The steps below describe
the release process, not an already available package. Desktop Names currently
has its own source build and explicit signing/setup flow; the Focus Python package
does not install it.

## Dependency policy

Runtime providers are extension dependencies. tmux is required only when an
enabled resource selects the bundled `tmux` specification. It is not a mandatory
Focus dependency, copied into the Python wheel, or vendored as a private binary.

- The Python package installs Focus and its bundled TOML specifications.
- Users install applications and provider executables required by their projects.
- `focus doctor` checks dependencies of enabled resources. A project using only
  GUI extensions can run without tmux.
- An extension can declare Homebrew repair metadata. `focus doctor --fix` shows
  the exact proposed command and requires approval before installation.

## Homebrew release

The formula template is `packaging/homebrew/focus.rb.in`. It declares Python for
Focus's runtime and deliberately does not declare tmux. Installing Focus must
not install every optional provider.

After publishing a source archive, calculate its SHA-256 digest and render the
formula with explicit release inputs:

```console
shasum -a 256 dist/local-workspace-manager-0.1.0.tar.gz
python3 scripts/render_homebrew_formula.py \
  --homepage https://github.com/AI-TECH-SU/work-cli \
  --url https://github.com/AI-TECH-SU/work-cli/archive/refs/tags/v0.1.0.tar.gz \
  --sha256 THE_64_CHARACTER_DIGEST \
  --version 0.1.0 \
  --output ../homebrew-tap/Formula/focus.rb
```

The release URL and digest are deliberately not guessed in this repository.
They become known only after a remote and immutable release artifact exist.
The rendered formula installs Python and `focus`, so the end-user command
is simply:

```console
brew install AI-TECH-SU/tap/focus
```

## pipx or source installation

Python package managers cannot install a macOS system executable. A user who
installs `focus` through pipx or from source can inspect and repair dependencies
declared by enabled extensions through the CLI:

```console
focus doctor --fix
```

For a configured tmux resource, the specification supplies `brew install tmux`.
The command shows the exact operation and asks for approval before running it.
Other dependencies require their own declaration and approval; a normal focus
command does not install packages. Non-interactive provisioning must opt in
explicitly:

```console
focus doctor --fix --yes
```

If Homebrew is unavailable, `focus` reports that boundary and points to
`https://brew.sh`; it does not download or execute an installer itself.
