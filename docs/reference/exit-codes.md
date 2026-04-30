# Exit codes

The kuroi CLI exits with one of the following codes. Pipelines should
treat any non-zero code as a failure.

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | Runtime failure — the operation could not complete. Examples: `kuroi doctor` reports a `PROBLEM` check, `kuroi undo` finds no backup to restore, `kuroi setup` cannot write the config file. The accompanying message on stderr describes the cause. |
| `2` | Bad usage or invalid input — argument or option parsing failed, the supplied config is invalid, the input PDF is missing or unreadable, or a provider/model/URL value is rejected. This includes Typer's default for argument-parsing failures. |
| `4` | Verification failed — `kuroi verify` detected residual leaks in the input PDF, or `kuroi run` detected leaks in the redacted output and refused to write the file. |

`kuroi verify` distinguishes pass and fail through stdout output and a
non-zero exit code (`4`) on failure. Wire it into your batch pipeline
as a gate by checking `$?` after the command.
