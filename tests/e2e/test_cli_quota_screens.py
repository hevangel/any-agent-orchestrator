"""Read live built-in quota screens; no model turn is submitted."""

import json
import os
import shutil

import pytest

from orchestrator.quota_screens import read_quota
from orchestrator.config import catalog
from orchestrator import quotas

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("cli", ["claude", "codex", "kiro", "antigravity", "cursor"])
def test_live_quota_screen(cli, evidence):
    if not shutil.which("herdr") or not shutil.which(catalog()[cli]["binary"]):
        pytest.skip(f"herdr or {cli} not installed")
    evidence.info(cli=cli, mode="herdr-quota-screen", host="local")
    result = read_quota(cli)
    evidence.text("screen.txt", result["screen"], label="Quota status screen")
    evidence.text("quota.json", json.dumps({k: v for k, v in result.items() if k != "screen"}, indent=2))
    assert not result["error"], result["error"]
    assert result["accounts"] and result["accounts"][0]["windows"]
    if cli == "antigravity":
        scopes = {w["scope"] for w in result["accounts"][0]["windows"]}
        assert scopes >= {"Gemini", "Claude/GPT"}
        if "Gemini 4 Argon Weekly Limit" in result["screen"]:
            assert "Argon" in scopes


def test_live_cursor_quota(evidence):
    if os.name != "nt" or not shutil.which("clanker-usage"):
        pytest.skip("Windows Cursor fallback requires clanker-usage")
    env = {k: v for k, v in os.environ.items() if k.upper() != "SSLKEYLOGFILE"}
    errors = {}
    rows = quotas.collect_cursor_windows([], errors, env)
    evidence.info(cli="cursor", mode="clanker-usage", host="local")
    evidence.text("quota.json", json.dumps({"accounts": rows, "errors": errors}, indent=2))
    assert not errors, errors
    assert rows and rows[0]["status"] in ("live", "cached") and rows[0]["windows"]
