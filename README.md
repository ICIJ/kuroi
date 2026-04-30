# kuroi

Strip sensitive data from PDFs with LLM assistance.

**Documentation:** https://icij.github.io/kuroi/

## Quick start

```sh
pip install kuroi
export ANTHROPIC_API_KEY=sk-ant-...
kuroi run document.pdf -o document.redacted.pdf
```

See the [Quick start guide](https://icij.github.io/kuroi/user-guide/quickstart/)
for the end-to-end walkthrough, including the diff/verify/undo cycle.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).

## Maintainers

- One-time setup for the docs site: in `Settings → Pages → Build and
  deployment`, set **Source** to **GitHub Actions**. The workflow at
  `.github/workflows/docs.yml` does the rest on every push to `main`.
