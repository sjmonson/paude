"""Initialize V2 configuration without modifying project files or later edits."""

from __future__ import annotations

import shlex

# This script runs with the container's Python, so it uses only the stdlib.
_INITIALIZE = """\
import json, os, pathlib, sys, tempfile
home = pathlib.Path(sys.argv[1])
target = home / '.config/opencode/opencode.json'
if target.exists() or target.with_suffix('.jsonc').exists():
    sys.exit(0)
snapshot = pathlib.Path('/pvc/.paude/agent-configs/opencode_v2.json')
def merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value
    return base
config = json.loads(sys.argv[2])
if snapshot.exists():
    config = merge(config, json.loads(snapshot.read_text()))
config['update'] = 'disable'
target.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(dir=target.parent)
try:
    with os.fdopen(fd, 'w') as output:
        json.dump(config, output, indent=2)
    os.replace(temporary, target)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
"""


def initialize_script(home: str, provider: str) -> str:
    """Return a safely quoted initialization command for the entrypoint."""
    from paude.agents.opencode_v2 import encoded_defaults

    return (
        "#!/bin/bash\npython3 -c "
        + shlex.quote(_INITIALIZE)
        + " "
        + shlex.quote(home)
        + " "
        + encoded_defaults(provider)
        + "\n"
    )
