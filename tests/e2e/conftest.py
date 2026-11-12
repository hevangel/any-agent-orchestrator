"""End-to-end test matrix, built from `.env` (see .env.example, section "End-to-end tests").

These tests send real prompts to real agents and spend a few hundred tokens per case on your
subscriptions, so they only run when selected: `uv run pytest -m e2e`.
"""

import shutil
import subprocess

import pytest

from orchestrator import config


def _docker_image_ready():
    if not shutil.which("docker"):
        return False
    probe = subprocess.run(["docker", "image", "inspect", "any-agent-orchestrator:latest"],
                           capture_output=True, text=True)
    return probe.returncode == 0


def cli_cases(settings=None):
    s = settings or config.Settings()
    known = config.catalog()
    wanted = s.list("AAO_E2E_CLIS") or ["claude", "codex"]
    modes = s.list("AAO_E2E_MODES") or ["native", "herdr"]
    account_mode = s.get("AAO_E2E_ACCOUNTS", "all")
    accounts = config.accounts(s)
    local = set(config.local_clis(s))
    cases = []

    def with_accounts(mode, host, cli, host_label):
        mine = [a for a in accounts if a.agent == cli and host_label in a.hosts]
        if account_mode == "default" or not mine:
            return [(mode, host, cli, None)]
        return [(mode, host, cli, a) for a in mine]

    for cli in wanted:
        if cli not in known or not known[cli].get("headless"):
            continue
        for mode in modes:
            if mode in ("native", "herdr", "herdr-agent") and cli in local:
                if mode == "herdr-agent" and not known[cli].get("herdr_kind"):
                    continue
                cases += with_accounts(mode, None, cli, "local")
            elif mode == "docker":
                cases.append((mode, None, cli, None))  # docker uses the compose credential mounts
            elif mode in ("ssh", "herdr-remote"):
                for host in config.hosts(s):
                    if cli not in host.clis or (mode == "herdr-remote" and not host.herdr):
                        continue
                    cases += with_accounts(mode, host, cli, host.label)
    return cases


def pytest_generate_tests(metafunc):
    if "cli_case" in metafunc.fixturenames:
        cases = cli_cases()
        ids = [f"{m}-{h.label if h else 'local'}-{c}-{a.id if a else 'default'}" for m, h, c, a in cases]
        metafunc.parametrize("cli_case", cases or [pytest.param(None, marks=pytest.mark.skip("no e2e cases configured"))],
                             ids=ids or ["none"])
    if "web_target" in metafunc.fixturenames:
        from orchestrator import web
        targets = config.Settings().list("AAO_E2E_WEB") or list(web.TARGETS)
        metafunc.parametrize("web_target", targets)


@pytest.fixture(scope="session")
def docker_ready():
    return _docker_image_ready()


@pytest.fixture(scope="session")
def e2e_timeout():
    return config.Settings().int("AAO_E2E_TIMEOUT", 180)
