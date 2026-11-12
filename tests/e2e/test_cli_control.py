"""Control every configured CLI agent in every configured run mode and account.

Each case asks the agent to add two random numbers and checks the answer, so a pass proves the
prompt reached the agent and the reply came back through that mode, on that account.
"""

import os
import shutil

import pytest

from orchestrator import drivers
from orchestrator.config import Settings

pytestmark = pytest.mark.e2e


def test_cli_answers(cli_case, docker_ready, e2e_timeout, tmp_path, evidence):
    mode, host, cli, account = cli_case
    evidence.info(mode=mode, host=host.label if host else "local", cli=cli, account=account.id if account else "default",
                  permission=drivers.permission_mode())
    if mode == "docker" and not docker_ready:
        pytest.skip("docker image any-agent-orchestrator:latest not built (docker compose build)")
    if mode.startswith("herdr") and not shutil.which("herdr"):
        pytest.skip("herdr not installed")
    if mode in ("ssh",) and not shutil.which("ssh"):
        pytest.skip("ssh not installed")

    prompt, expect = drivers.check_prompt()
    # A fresh folder on purpose: herdr-agent must get past the agent's folder-trust screen by itself
    # (scripted answer from agents.json startup_forms; AAO_TRUST_FOLDERS=0 turns that off).
    cwd = os.path.expanduser(Settings().get("AAO_E2E_AGENT_CWD", str(tmp_path))) if mode == "herdr-agent" else \
        (str(tmp_path) if mode in ("herdr", "docker") else None)
    model = Settings().get(f"AAO_E2E_MODEL_{cli}") or None  # e.g. a free model, so tests cost nothing
    result = drivers.send(cli, mode, prompt, expect, account=account, host=host, timeout=e2e_timeout, cwd=cwd,
                          model=model)
    evidence.info(seconds=result.seconds, command=result.command, expected=expect, model=model or "default")
    evidence.text("transcript.txt", f"mode: {mode}\nhost: {host.label if host else 'local'}\ncli: {cli}\n"
                  f"account: {account.id if account else 'default'}\nprompt: {prompt}\nexpected: {expect}\n"
                  f"command: {result.command}\nok: {result.ok}\nseconds: {result.seconds}\nerror: {result.error}\n"
                  f"\n--- output / terminal ---\n{result.output}", label="Transcript")
    assert result.ok, (f"{cli} via {mode}{' on ' + host.label if host else ''}"
                       f"{' as ' + account.id if account else ''} failed after {result.seconds}s\n"
                       f"command: {result.command}\nerror: {result.error}\noutput tail:\n{result.output[-800:]}")
