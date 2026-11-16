"""Shared agent configuration selection, validation, and snapshot handlers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import json5

if TYPE_CHECKING:
    from paude.config.resolver import Source


@dataclass(frozen=True)
class AgentConfigSnapshot:
    """Validated configuration; payloads must never appear in CLI output."""

    path: Path
    source: Source
    content: str = field(repr=False)


# Handler targets are fixed paths, never supplied by the user.
CONFIG_HANDLERS = {"opencode_v2": ".paude/agent-configs/opencode_v2.json"}


def config_paths(value: object, directory: Path) -> dict[str, str]:
    """Normalize a config-file mapping relative to its source file."""
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) or not item
        for key, item in value.items()
    ):
        raise ValueError("agent-configs must be an object mapping agents to file paths")
    return {key: str(_absolute_path(item, directory)) for key, item in value.items()}


def _absolute_path(value: str, directory: Path) -> Path:
    path = Path(value).expanduser()
    return (directory / path).resolve() if not path.is_absolute() else path.resolve()


def parse_config_options(options: list[str] | None) -> dict[str, str]:
    """Parse repeatable AGENT=PATH flags without splitting commas in paths."""
    result: dict[str, str] = {}
    for option in options or []:
        name, separator, path = option.partition("=")
        if not separator or not name or not path:
            raise ValueError("--agent-config requires AGENT=PATH")
        if name in result:
            raise ValueError(f"Duplicate --agent-config for '{name}'")
        result[name] = str(_absolute_path(path, Path.cwd()))
    return result


def resolve_agent_configs(
    agents: list[str],
    user: dict[str, str],
    project: dict[str, str],
    cli: dict[str, str],
) -> dict[str, AgentConfigSnapshot]:
    """Resolve each agent independently and validate before creating resources."""
    from paude.agents import list_agents

    selected: dict[str, tuple[str, Source]] = {}
    layers: list[tuple[dict[str, str], Source]] = [
        (user, "user defaults"),
        (project, "paude.json"),
        (cli, "cli"),
    ]
    for mapping, source in layers:
        for name, configured_path in mapping.items():
            selected[name] = (configured_path, source)
    result: dict[str, AgentConfigSnapshot] = {}
    for name, (value, provenance) in selected.items():
        if name not in list_agents():
            raise ValueError(f"Unknown agent in agent-configs: '{name}'")
        if name not in agents:
            raise ValueError(f"Configuration references unselected agent '{name}'")
        if name not in CONFIG_HANDLERS:
            raise ValueError(f"Agent '{name}' does not support base configuration")
        path = Path(value)
        try:
            data: Any = json5.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            # Parser errors can quote secret-bearing input; do not echo them.
            raise ValueError(
                f"Cannot read JSON/JSONC agent configuration: {path}"
            ) from None
        if not isinstance(data, dict):
            raise ValueError(f"Agent configuration must contain an object: {path}")
        try:
            content = json.dumps(data, allow_nan=False)
        except ValueError:
            raise ValueError(
                f"Agent configuration contains non-finite numbers: {path}"
            ) from None
        result[name] = AgentConfigSnapshot(path, provenance, content)
    return result
