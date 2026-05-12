# kuroi

<div align="center">

|      | Status |
| ---: | :--- |
| **CI checks** | [![CI](https://img.shields.io/github/actions/workflow/status/ICIJ/kuroi/ci.yml?style=flat-square)](https://github.com/ICIJ/kuroi/actions/workflows/ci.yml) |
| **Latest version** | [![PyPI](https://img.shields.io/pypi/v/kuroi?style=flat-square&color=success)](https://pypi.org/project/kuroi/) |
| **Release date** | [![Release date](https://img.shields.io/github/release-date/ICIJ/kuroi?style=flat-square&color=success)](https://github.com/ICIJ/kuroi/releases/latest) |
| **Python** | [![Python](https://img.shields.io/pypi/pyversions/kuroi?style=flat-square)](https://pypi.org/project/kuroi/) |
| **License** | [![License](https://img.shields.io/github/license/ICIJ/kuroi?style=flat-square)](https://github.com/ICIJ/kuroi/blob/main/LICENSE) |
| **Open issues** | [![Open issues](https://img.shields.io/github/issues/ICIJ/kuroi?style=flat-square&color=success)](https://github.com/ICIJ/kuroi/issues) |

Strip sensitive data from PDFs with LLM assistance.

<img src="docs/assets/icon-512.png" alt="kuroi" width="160" />

</div>

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

