"""OpenCode V2 behavior, initialization, and file exclusion tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paude.agents import get_agent, get_agents, list_agents
from paude.agents.opencode_v2 import OpenCodeV2Agent, provider_defaults
from paude.agents.opencode_v2_config import initialize_script
from paude.backends.base import SessionConfig
from paude.backends.podman.legacy_state import backup_exclude_globs
from paude.backends.session_env import build_session_env


def test_registry_install_and_persistence() -> None:
    agent = get_agent("opencode_v2")
    assert "opencode_v2" in list_agents()
    assert isinstance(agent, OpenCodeV2Agent)
    assert agent.config.provider == "anthropic"
    assert agent.config.persistent_dir_names == [
        ".config/opencode",
        ".local/share/opencode",
        ".local/state/opencode",
    ]
    lines = "\n".join(agent.dockerfile_install_lines("/home/paude"))
    assert "https://opencode.ai/v2/install" in lines
    assert "test -x /home/paude/.opencode/bin/opencode" in lines
    assert "/etc/opencode/opencode.json" in lines
    assert '"update":"disable"' in lines
    assert "pipefail" in lines
    assert agent.host_config_mounts(Path("/host")) == []


@pytest.mark.parametrize("provider", ["anthropic", "openai", "vertex"])
def test_providers(provider: str) -> None:
    agent = get_agent("opencode_v2", provider)
    assert agent.config.provider == provider
    defaults = provider_defaults(provider)
    assert "providers" in defaults
    if provider == "vertex":
        providers = defaults["providers"]
        assert isinstance(providers, dict)
        assert "google-vertex" in providers
        assert "VERTEX_LOCATION" in agent.config.passthrough_env_vars
    else:
        assert f"{provider.upper()}_API_KEY" in agent.config.secret_env_vars


def test_chatgpt_is_not_supported() -> None:
    with pytest.raises(ValueError, match="does not support"):
        get_agent("opencode_v2", "chatgpt")


@pytest.mark.parametrize("yolo", [False, True])
def test_launch_and_proxy_environment(tmp_path: Path, yolo: bool) -> None:
    agent = OpenCodeV2Agent()
    config = SessionConfig(name="v2", workspace=tmp_path, image="image", yolo=yolo)
    env, args = build_session_env(config, agent, proxy_name="proxy")
    assert env["PAUDE_AGENT_LAUNCH_CMD"] == "opencode --standalone"
    assert ("--auto" in args) == yolo
    assert env["ANTHROPIC_API_KEY"] == "paude-proxy-managed"
    assert "localhost" in env["NO_PROXY"]
    assert agent.build_environment()["OPENCODE_DISABLE_AUTOUPDATE"] == "true"
    assert env["NODE_EXTRA_CA_CERTS"]
    assert agent.launch_command('run "hello"') == 'opencode --standalone run "hello"'


def test_initialization_merge_and_preservation(tmp_path: Path) -> None:
    volume = tmp_path / "volume"
    snapshot = volume / ".paude/agent-configs/opencode_v2.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(
        json.dumps(
            {
                "providers": {
                    "anthropic": {"settings": {"baseURL": "https://example.test"}}
                },
                "plugins": ["custom"],
                "model": "anthropic/custom",
            }
        )
    )
    home = tmp_path / "home with spaces and ' quotes"
    script = initialize_script(str(home), "anthropic").replace(
        "/pvc/", str(volume) + "/"
    )
    subprocess.run(["bash", "-c", script], check=True)
    target = home / ".config/opencode/opencode.json"
    actual = json.loads(target.read_text())
    assert actual["providers"]["anthropic"]["settings"] == {
        "apiKey": "{env:ANTHROPIC_API_KEY}",
        "baseURL": "https://example.test",
    }
    assert actual["plugins"] == ["custom"]
    assert actual["model"] == "anthropic/custom"
    assert not (tmp_path / "opencode.json").exists()
    target.write_text('{"session":"edited"}')
    snapshot.unlink()
    subprocess.run(["bash", "-c", script], check=True)
    assert json.loads(target.read_text()) == {"session": "edited"}


def test_existing_jsonc_is_preserved(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = home / ".config/opencode/opencode.jsonc"
    target.parent.mkdir(parents=True)
    target.write_text('// custom\n{"model":"custom/model",}')
    script = initialize_script(str(home), "openai").replace(
        "/pvc/", str(tmp_path) + "/"
    )
    subprocess.run(["bash", "-c", script], check=True)
    assert not target.with_suffix(".json").exists()
    assert "// custom" in target.read_text()


@pytest.mark.parametrize("reuse_volume", [False, True])
def test_snapshot_transfer_failure_rolls_back(
    tmp_path: Path, reuse_volume: bool
) -> None:
    from tests.test_podman_session import _make_create_session_backend

    runner = MagicMock()
    runner.container_exists.return_value = False
    volume = MagicMock()
    backend = _make_create_session_backend(runner, volume)
    config = SessionConfig(
        name="v2",
        workspace=tmp_path,
        image="image",
        agent="opencode_v2",
        reuse_volume=reuse_volume,
    )
    with patch(
        "paude.container.agent_configs.seed_agent_configs",
        side_effect=RuntimeError("transfer failed"),
    ):
        with pytest.raises(RuntimeError, match="transfer failed"):
            backend.create_session(config)
    runner.create_container.assert_not_called()
    if reuse_volume:
        volume.remove_volume.assert_not_called()
    else:
        volume.remove_volume.assert_called_once()


def test_default_initialization_without_snapshot(tmp_path: Path) -> None:
    home = tmp_path / "home"
    script = initialize_script(str(home), "vertex").replace(
        "/pvc/", str(tmp_path) + "/"
    )
    subprocess.run(["bash", "-c", script], check=True)
    target = home / ".config/opencode/opencode.json"
    config = json.loads(target.read_text())
    assert config == {**provider_defaults("vertex"), "update": "disable"}
    assert target.stat().st_mode & 0o777 == 0o600


def test_credential_file_exclusions() -> None:
    exclusions = backup_exclude_globs(get_agents(["opencode_v2"]))
    assert ".local/share/opencode/auth.json" in exclusions
    assert ".local/share/opencode/mcp-auth.json" in exclusions
