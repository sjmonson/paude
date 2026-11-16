"""OpenCode V2 agent integration."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from paude.agents.base import (
    AgentConfig,
    build_environment_from_config,
    build_provider_credentials,
    pipefail_install_lines,
)


class OpenCodeV2Agent:
    """Run OpenCode V2 with a private server and session-owned configuration."""

    def __init__(self, provider: str | None = None) -> None:
        creds = build_provider_credentials("opencode_v2", provider)
        self._config = AgentConfig(
            name="opencode_v2",
            display_name="OpenCode V2",
            process_name="opencode",
            session_name="opencode_v2",
            install_script=(
                "curl -fsSL https://opencode.ai/v2/install | "
                "bash -s -- --no-modify-path"
            ),
            install_dir=".opencode/bin",
            env_vars={"OPENCODE_DISABLE_AUTOUPDATE": "true", **creds.extra_env_vars},
            passthrough_env_vars=creds.passthrough_env_vars,
            secret_env_vars=creds.secret_env_vars,
            passthrough_env_prefixes=creds.passthrough_env_prefixes,
            config_dir_name=".config/opencode",
            extra_persistent_dir_names=[
                ".local/share/opencode",
                ".local/state/opencode",
            ],
            config_file_name=None,
            credential_file_names=[
                ".local/share/opencode/auth.json",
                ".local/share/opencode/mcp-auth.json",
            ],
            activity_files=[],
            yolo_flag="--auto",
            clear_command=None,
            extra_domain_aliases=["opencode"],
            provider=creds.resolved_provider_name,
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        return [
            "",
            "# Disable OpenCode V2 updates in managed system configuration",
            "USER root",
            "RUN mkdir -p /etc/opencode && "
            "printf '%s\\n' '{\"update\":\"disable\"}' > /etc/opencode/opencode.json",
            "# Install OpenCode V2",
            "USER paude",
            f"WORKDIR {container_home}",
            *pipefail_install_lines(self._config, container_home),
            f'ENV PATH="{container_home}/{self._config.install_dir}:$PATH"',
        ]

    def apply_sandbox_config(
        self, home: str, workspace: str, args: str, *, yolo: bool = False
    ) -> str:
        from paude.agents.opencode_v2_config import initialize_script

        return initialize_script(home, self._config.provider or "anthropic")

    def launch_command(self, args: str) -> str:
        return "opencode --standalone" + (f" {args}" if args else "")

    def host_config_mounts(self, home: Path) -> list[str]:
        return []

    def build_environment(self) -> dict[str, str]:
        return build_environment_from_config(self._config)


def provider_defaults(provider: str) -> dict[str, object]:
    """V2 provider settings use references resolved inside the container."""
    if provider == "vertex":
        name = "google-vertex"
        settings = {
            "project": "{env:GOOGLE_CLOUD_PROJECT}",
            "location": "{env:VERTEX_LOCATION}",
        }
    else:
        name = provider
        variable = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
        settings = {"apiKey": "{env:" + variable + "}"}
    return {
        "$schema": "https://opencode.ai/config.json",
        "share": "disabled",
        "providers": {name: {"settings": settings}},
    }


def encoded_defaults(provider: str) -> str:
    """Quote defaults for a Python command's argument."""
    return shlex.quote(json.dumps(provider_defaults(provider)))
