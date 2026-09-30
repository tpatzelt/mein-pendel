"""README.md must document every PENDEL_*/env var from deploy/.pendel.env.example
and the three local entrypoints (charter G4). Offline: reads files only."""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_ENV_VAR_LINE = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=")


def _env_var_names(env_example_text: str) -> list[str]:
    names = []
    for line in env_example_text.splitlines():
        match = _ENV_VAR_LINE.match(line)
        if match:
            names.append(match.group(1))
    return names


def test_readme_documents_every_env_example_variable() -> None:
    env_example = (_REPO_ROOT / "deploy" / ".pendel.env.example").read_text()
    readme = (_REPO_ROOT / "README.md").read_text()

    names = _env_var_names(env_example)
    assert names, "expected at least one variable in deploy/.pendel.env.example"

    missing = [name for name in names if name not in readme]
    assert not missing, f"README.md is missing env vars: {missing}"


def test_readme_documents_entrypoints_and_deploy_doc() -> None:
    readme = (_REPO_ROOT / "README.md").read_text()

    for needle in ("pendel.app:app", "pendel.runner", "pendel.telegram_poll", "DEPLOY.md"):
        assert needle in readme, f"README.md is missing {needle!r}"
