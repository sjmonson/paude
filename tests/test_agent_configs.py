"""Base configuration validation and snapshot transport tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from paude.config.agent_configs import (
    AgentConfigSnapshot,
    config_paths,
    parse_config_options,
    resolve_agent_configs,
)
from paude.config.parser import parse_config
from paude.config.user_config import load_user_defaults
from paude.container.agent_configs import _SEED, seed_agent_configs


def test_jsonc_and_snapshot_independence(tmp_path: Path) -> None:
    path = tmp_path / "base.jsonc"
    path.write_text('{// comment\n"model":"anthropic/example", "plugins":[],}')
    snapshot = resolve_agent_configs(
        ["opencode_v2"], {}, {}, {"opencode_v2": str(path)}
    )["opencode_v2"]
    path.unlink()
    assert json.loads(snapshot.content)["model"] == "anthropic/example"
    assert snapshot.source == "cli"
    assert "anthropic/example" not in repr(snapshot)


@pytest.mark.parametrize("layer", ["cli", "paude.json", "user defaults"])
def test_precedence(tmp_path: Path, layer: str) -> None:
    path = tmp_path / "base.json"
    path.write_text('{"value": 3}')
    good = {"opencode_v2": str(path)}
    bad = {"opencode_v2": "/missing"}
    user, project, cli = {
        "cli": (bad, bad, good),
        "paude.json": (bad, good, {}),
        "user defaults": (good, {}, {}),
    }[layer]
    snapshot = resolve_agent_configs(["opencode_v2"], user, project, cli)
    assert snapshot["opencode_v2"].source == layer


@pytest.mark.parametrize(
    "content", ["[]", "null", '"secret"', '{"token": "SECRET" broken}', '{"x": NaN}']
)
def test_invalid_content_does_not_disclose_values(tmp_path: Path, content: str) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(content)
    with pytest.raises(ValueError, match="configuration") as error:
        resolve_agent_configs(["opencode_v2"], {}, {}, {"opencode_v2": str(path)})
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize(
    ("agents", "name", "message"),
    [
        (["opencode_v2"], "missing", "Unknown agent"),
        (["claude"], "opencode_v2", "unselected"),
        (["claude"], "claude", "does not support"),
    ],
)
def test_invalid_handler(agents: list[str], name: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_agent_configs(agents, {}, {}, {name: "/missing"})


@pytest.mark.parametrize(
    "options", [["bad"], ["=file"], ["opencode_v2="], ["a=x", "a=y"]]
)
def test_invalid_options(options: list[str]) -> None:
    with pytest.raises(ValueError, match="agent-config"):
        parse_config_options(options)


def test_paths_preserve_commas_equals_and_shell_characters(tmp_path: Path) -> None:
    name = "a,b=c $(touch SENTINEL).jsonc"
    assert parse_config_options([f"opencode_v2={name}"]) == {
        "opencode_v2": str(tmp_path / name)
    }
    assert not (tmp_path / "SENTINEL").exists()


def test_config_file_relative_paths(tmp_path: Path) -> None:
    directory = tmp_path / "configs"
    directory.mkdir()
    mapping = {"opencode_v2": "base.jsonc"}
    project = directory / "paude.json"
    project.write_text(json.dumps({"create": {"agent-configs": mapping}}))
    user = directory / "defaults.json"
    user.write_text(json.dumps({"defaults": {"agent-configs": mapping}}))
    expected = {"opencode_v2": str(directory / "base.jsonc")}
    assert parse_config(project).create_agent_configs == expected
    assert load_user_defaults(user).agent_configs == expected


@pytest.mark.parametrize("value", [[], None, {"a": 12}, {"a": ""}])
def test_invalid_config_mapping(value: object, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="agent-configs"):
        config_paths(value, tmp_path)


@pytest.mark.parametrize("binary", ["podman", "docker"])
@pytest.mark.parametrize("remote", [False, True])
def test_seed_uses_stdin(binary: str, remote: bool, tmp_path: Path) -> None:
    engine = MagicMock(binary=binary, is_remote=remote)
    payload_content = '{"value":"do not print me"}'
    snapshot = AgentConfigSnapshot(tmp_path / "base.json", "cli", payload_content)
    seed_agent_configs(engine, "image", "volume", {"opencode_v2": snapshot})
    args, kwargs = engine.run.call_args
    assert payload_content not in " ".join(args)
    assert (
        json.loads(kwargs["input"])[".paude/agent-configs/opencode_v2.json"]
        == payload_content
    )
    assert args[:3] == ("run", "--rm", "-i")
    assert "--network" in args
    assert "none" in args


def test_seed_preserves_existing_snapshot(tmp_path: Path) -> None:
    script = _SEED.replace("pathlib.Path('/pvc')", "pathlib.Path(sys.argv[1])")
    script = script.replace("pwd.getpwnam('paude')", "pwd.getpwuid(os.getuid())")
    payload = {".paude/agent-configs/opencode_v2.json": '{"first":true}'}
    for value in [payload, {next(iter(payload)): "replacement"}]:
        subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            input=json.dumps(value),
            text=True,
            check=True,
        )
    target = tmp_path / next(iter(payload))
    assert target.read_text() == '{"first":true}'
    assert target.stat().st_mode & 0o777 == 0o600


def test_empty_snapshots_do_not_start_helper() -> None:
    engine = MagicMock()
    seed_agent_configs(engine, "image", "volume", {})
    engine.run.assert_not_called()
