"""Static structure tests for the G4 deploy files (charter G4).

These are stdlib text checks only -- PyYAML is not a project dependency,
so the compose and workflow files are asserted against as plain text, not
parsed. `docker build .` and `docker compose config` are left to a human
because the NIGHTSHIFT sandbox blocks every Docker call (see DEPLOY.md).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text()
COMPOSE = (ROOT / "deploy" / "compose.yaml").read_text()
ENV_EXAMPLE = (ROOT / "deploy" / ".pendel.env.example").read_text()
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yaml").read_text()
DEPLOY_MD = (ROOT / "DEPLOY.md").read_text()

# Vars whose reading code treats an empty string as unset (an `x or default`
# / `x or None` pattern), so shipping them uncommented but empty is safe.
_OR_FALLBACK_KEYS = {"PENDEL_TELEGRAM_BOT_TOKEN", "PENDEL_NTFY_URL"}
_NUMERIC_KEYS = {"PENDEL_CHECK_LEAD_MIN", "PENDEL_RATE_LIMIT_PER_MIN"}


def _healthcheck_block(dockerfile: str) -> str:
    lines = dockerfile.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("HEALTHCHECK"):
            block = [line]
            j = i
            while block[-1].rstrip().endswith("\\") and j + 1 < len(lines):
                j += 1
                block.append(lines[j])
            return "\n".join(block)
    raise AssertionError("no HEALTHCHECK instruction found in Dockerfile")


def _uncommented_assignments(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert "=" in stripped, f"malformed env line: {line!r}"
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _service_block(compose: str, service: str) -> str:
    lines = compose.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == f"{service}:":
            start = i
            break
    assert start is not None, f"service {service!r} not found in compose.yaml"
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line and not line.startswith((" ", "\t")):
            break
        block.append(line)
    return "\n".join(block)


# -- Dockerfile ---------------------------------------------------------


def test_dockerfile_runs_as_non_root_user():
    match = re.search(r"^USER\s+(\S+)\s*$", DOCKERFILE, re.MULTILINE)
    assert match, "Dockerfile has no USER instruction"
    assert match.group(1) != "root"


def test_dockerfile_healthcheck_hits_healthz_with_urllib():
    block = _healthcheck_block(DOCKERFILE)
    assert "/healthz" in block
    assert "urllib" in block


def test_dockerfile_from_images_are_pinned_with_a_tag():
    froms = re.findall(r"^FROM\s+(\S+)", DOCKERFILE, re.MULTILINE)
    assert froms, "no FROM instruction found"
    for ref in froms:
        image = ref.split("@")[0]
        assert ":" in image.rsplit("/", 1)[-1], f"{ref!r} has no tag"
        assert not image.endswith(":latest"), f"{ref!r} is pinned to :latest"


def test_dockerfile_cmd_never_uses_bare_uv_run():
    cmd_lines = [
        line for line in DOCKERFILE.splitlines() if line.strip().startswith("CMD")
    ]
    assert cmd_lines, "no CMD instruction found"
    for line in cmd_lines:
        assert "uv run" not in line


def test_dockerfile_sets_data_dir_to_data():
    assert "PENDEL_DATA_DIR=/data" in DOCKERFILE


# -- compose.yaml ---------------------------------------------------------


def test_compose_has_no_ports_key():
    assert not re.search(r"^\s*ports:", COMPOSE, re.MULTILINE)


def test_compose_never_uses_bare_uv_run():
    assert "uv run" not in COMPOSE


def test_compose_follows_homelab_conventions():
    assert "caddy_network" in COMPOSE
    assert "external: true" in COMPOSE
    assert "/opt/dockerdata/pendel" in COMPOSE
    assert "env_file" in COMPOSE


def test_compose_only_web_joins_caddy_network():
    web = _service_block(COMPOSE, "web")
    scheduler = _service_block(COMPOSE, "scheduler")
    telegram = _service_block(COMPOSE, "telegram")
    assert "caddy_network" in web
    assert "caddy_network" not in scheduler
    assert "caddy_network" not in telegram


def test_compose_scheduler_disables_healthcheck():
    scheduler = _service_block(COMPOSE, "scheduler")
    assert "healthcheck" in scheduler
    assert re.search(r"disable:\s*true", scheduler)


def test_compose_telegram_service_runs_the_poller():
    telegram = _service_block(COMPOSE, "telegram")
    assert '"/app/.venv/bin/python", "-m", "pendel.telegram_poll"' in telegram


def test_compose_telegram_service_disables_healthcheck():
    telegram = _service_block(COMPOSE, "telegram")
    assert "healthcheck" in telegram
    assert re.search(r"disable:\s*true", telegram)


def test_compose_telegram_service_restarts_on_failure_only():
    telegram = _service_block(COMPOSE, "telegram")
    assert re.search(r"restart:\s*on-failure", telegram), (
        "telegram exits 0 when PENDEL_TELEGRAM_BOT_TOKEN is unset; "
        "unless-stopped would restart that exit-0 process in a loop"
    )
    assert not re.search(r"restart:\s*unless-stopped", telegram)


def test_compose_telegram_service_has_no_ports():
    telegram = _service_block(COMPOSE, "telegram")
    assert not re.search(r"^\s*ports:", telegram, re.MULTILINE)


# -- deploy/.pendel.env.example -------------------------------------------


def test_env_example_numeric_vars_are_valid_positive_ints_when_present():
    values = _uncommented_assignments(ENV_EXAMPLE)
    for key in _NUMERIC_KEYS:
        if key in values:
            assert int(values[key]) > 0, f"{key} must be a positive integer"


def test_env_example_has_no_empty_uncommented_pendel_vars_outside_fallback():
    values = _uncommented_assignments(ENV_EXAMPLE)
    for key, value in values.items():
        if key.startswith("PENDEL_") and value == "":
            assert key in _OR_FALLBACK_KEYS, (
                f"{key} is shipped empty but its code does not treat "
                "an empty string as unset"
            )


def test_env_example_forwarded_allow_ips_is_commented_out():
    assert re.search(r"^#\s*FORWARDED_ALLOW_IPS=", ENV_EXAMPLE, re.MULTILINE)
    assert not re.search(r"^FORWARDED_ALLOW_IPS=", ENV_EXAMPLE, re.MULTILINE)


def test_env_example_forwarded_allow_ips_is_never_a_wildcard():
    for line in ENV_EXAMPLE.splitlines():
        if "FORWARDED_ALLOW_IPS" in line:
            assert "*" not in line, (
                "FORWARDED_ALLOW_IPS must never be '*': any other container "
                "on caddy_network could spoof X-Forwarded-For"
            )


def test_env_example_lists_every_pendel_var_used_in_source():
    src_dir = ROOT / "src" / "pendel"
    found: set[str] = set()
    for path in src_dir.rglob("*.py"):
        found.update(re.findall(r"PENDEL_[A-Z_]+", path.read_text()))
    # Covered even before T-0033 (the rate limiter) lands.
    found.add("PENDEL_RATE_LIMIT_PER_MIN")
    for var in sorted(found):
        assert re.search(rf"^#?\s*{var}=", ENV_EXAMPLE, re.MULTILINE), (
            f"{var} is read in src/pendel but missing from the env example"
        )


# -- .github/workflows/ci.yaml ---------------------------------------------


def test_workflow_runs_tests_and_publishes_expected_tags():
    assert "uv run pytest -q" in WORKFLOW
    assert "ghcr.io/tpatzelt/mein-pendel:latest" in WORKFLOW
    assert "sha-" in WORKFLOW


# -- YAML formatting --------------------------------------------------------


def test_yaml_files_contain_no_tabs():
    assert "\t" not in COMPOSE
    assert "\t" not in WORKFLOW


# -- DEPLOY.md ---------------------------------------------------------


def test_deploy_md_mentions_the_correct_env_file_path():
    assert "deploy/.pendel.env" in DEPLOY_MD


def test_deploy_md_never_places_env_file_under_data_volume():
    for line in DEPLOY_MD.splitlines():
        if ".pendel.env" in line:
            assert "/opt/dockerdata" not in line, line


def test_deploy_md_states_homelab_repo_is_not_edited():
    lowered = DEPLOY_MD.lower()
    assert "homelab repo" in lowered
    assert "not edited" in lowered or "no changes" in lowered


def test_deploy_md_documents_forwarded_allow_ips_subnet_lookup():
    assert "FORWARDED_ALLOW_IPS" in DEPLOY_MD
    assert "docker network inspect caddy_network" in DEPLOY_MD
