"""Seed private configuration snapshots in a session volume at creation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from paude.config.agent_configs import CONFIG_HANDLERS, AgentConfigSnapshot

if TYPE_CHECKING:
    from paude.container.engine import ContainerEngine

_SEED = """\
import json, os, pathlib, pwd, sys, tempfile
account = pwd.getpwnam('paude')
for relative, content in json.load(sys.stdin).items():
    target = pathlib.Path('/pvc') / relative
    if target.exists():
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    for parent in (target.parent.parent, target.parent):
        os.chown(parent, account.pw_uid, account.pw_gid)
    fd, temporary = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(content)
        os.chown(temporary, account.pw_uid, account.pw_gid)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
"""


def seed_agent_configs(
    engine: ContainerEngine,
    image: str,
    volume: str,
    snapshots: dict[str, AgentConfigSnapshot],
) -> None:
    """Transfer via stdin, including over SSH; never expose payloads in argv."""
    if not snapshots:
        return
    payload = {CONFIG_HANDLERS[name]: item.content for name, item in snapshots.items()}
    engine.run(
        "run",
        "--rm",
        "-i",
        "--network",
        "none",
        "--user",
        "root",
        "--security-opt",
        "label=disable",
        "-v",
        f"{volume}:/pvc",
        "--entrypoint",
        "python3",
        image,
        "-c",
        _SEED,
        input=json.dumps(payload),
    )
