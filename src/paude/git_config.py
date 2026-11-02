"""Create portable snapshots of the invoking user's global Git configuration."""

from __future__ import annotations

import subprocess
import tempfile
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _quote(value: str) -> str:
    return (
        '"'
        + (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\t", "\\t")
            .replace("\b", "\\b")
        )
        + '"'
    )


def _serialize(data: str) -> str:
    lines: list[str] = []
    for record in data.split("\0"):
        if not record:
            continue
        key, separator, value = record.partition("\n")
        section, _, rest = key.partition(".")
        subsection, dot, variable = rest.rpartition(".")
        if not dot:
            variable = rest
        if variable.lower() == "path" and section.lower() in {"include", "includeif"}:
            continue
        header = section + (" " + _quote(subsection) if dot else "")
        lines.append(
            f"[{header}]\n\t{variable}"
            + (" = " + _quote(value) if separator else "")
            + "\n"
        )
    return "".join(lines)


@contextmanager
def global_git_snapshot() -> Iterator[Path | None]:
    """Yield a temporary flattened config, or None when Git cannot read it."""
    try:
        result = subprocess.run(
            ["git", "config", "--includes", "--null", "--list", "--show-scope"],
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        warnings.warn("Unable to read global Git configuration", stacklevel=2)
        yield None
        return
    if result.returncode:
        warnings.warn("Unable to read global Git configuration", stacklevel=2)
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="paude-git-") as directory:
        path = Path(directory) / ".gitconfig"
        records = result.stdout.split("\0")
        global_records = "\0".join(
            record
            for scope, record in zip(records[::2], records[1::2], strict=False)
            if scope == "global"
        )
        path.write_text(_serialize(global_records))
        path.chmod(0o600)
        yield path
