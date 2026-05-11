# Install

kuroi runs on Python 3.12 or newer. Pick whichever installation method
matches your workflow.

## Installation methods

=== "pip"

    ```sh
    pip install kuroi
    ```

=== "pipx"

    ```sh
    pipx install kuroi
    ```

    `pipx` installs `kuroi` into a dedicated virtual environment and
    exposes it on your PATH, keeping it isolated from your system Python
    and other projects.

=== "uv (recommended for end users)"

    ```sh
    uv tool install kuroi
    ```

    `uv tool install` puts `kuroi` on your PATH inside an isolated
    environment, so it never collides with your project dependencies.

=== "From source"

    ```sh
    git clone https://github.com/ICIJ/kuroi.git
    cd kuroi
    pip install -e .
    ```

## System requirements

- **Python 3.12 or newer.** Earlier versions are not supported.
- **An LLM provider.** One of:
    - An Anthropic API key (cloud, per-token).
    - A Claude Code subscription (cloud, flat-rate).
    - A running [Ollama](https://ollama.ai) instance (local, free).

    See [LLM providers](providers.md) for setup details.
- **PDFs you have permission to redact.** kuroi never uploads files; only
  extracted text snippets are sent to the LLM.

## Verify the install

```sh
$ kuroi --version
kuroi 0.1.0

$ kuroi doctor
  kuroi version 0.1.0                                                        ok
  Python version 3.12.4                                                      ok
  Anthropic API key                set (env: ANTHROPIC_API_KEY)              ok
  tesseract                        /usr/bin/tesseract                        ok
  qpdf                             /usr/bin/qpdf                             ok
  Provider                         anthropic                                 ok
  Model                            claude-opus-4-7                           ok

Everything looks good.
```

`kuroi doctor` checks the Python version, your Anthropic API key, the
optional `tesseract` and `qpdf` binaries, and the resolved provider /
model. When `provider = ollama`, it also probes the Ollama daemon's
`/api/tags` endpoint so you know whether it's reachable.

If `kuroi doctor` flags any issue, the [Troubleshooting](troubleshooting.md)
page lists the common fixes.

## Next steps

- [Quick start](quickstart.md) — redact your first PDF.
- [LLM providers](providers.md) — set up Anthropic or Ollama.
