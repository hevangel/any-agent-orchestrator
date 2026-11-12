import re

from orchestrator import accounts, config, drivers

ENV = {
    "AAO_HOSTS": "box",
    "AAO_HOST_box_SSH": "me@box",
    "AAO_HOST_box_HERDR": "box",
    "AAO_HOST_box_CLIS": "claude,codex",
    "AAO_ACCOUNTS": "c_paid,c_free,c_work,h_prof,cur_key",
    "AAO_ACCOUNT_c_paid_AGENT": "claude", "AAO_ACCOUNT_c_paid_TIER": "paid", "AAO_ACCOUNT_c_paid_PRIORITY": "1",
    "AAO_ACCOUNT_c_paid_HOSTS": "local,box",
    "AAO_ACCOUNT_c_free_AGENT": "claude", "AAO_ACCOUNT_c_free_TIER": "free", "AAO_ACCOUNT_c_free_HOME": "~/.claude-free",
    "AAO_ACCOUNT_c_work_AGENT": "claude", "AAO_ACCOUNT_c_work_TIER": "paid", "AAO_ACCOUNT_c_work_PRIORITY": "2",
    "AAO_ACCOUNT_c_work_HOME": "~/.claude-work",
    "AAO_ACCOUNT_h_prof_AGENT": "hermes", "AAO_ACCOUNT_h_prof_HOME": "coder",
    "AAO_ACCOUNT_cur_key_AGENT": "cursor", "AAO_ACCOUNT_cur_key_SECRET_ENV": "CURSOR_KEY_2",
    "AAO_E2E_MODES": "native,ssh",
}


def settings(extra=None):
    return config.Settings(env={**ENV, **(extra or {})})


def test_hosts_and_accounts_parse_case_insensitively():
    s = settings()
    (host,) = config.hosts(s)
    assert (host.label, host.ssh, host.herdr, host.clis) == ("box", "me@box", "box", ["claude", "codex"])
    by_id = {a.id: a for a in config.accounts(s)}
    assert by_id["c_paid"].hosts == ["local", "box"] and by_id["c_paid"].priority == 1
    assert config.validate(s) == []


def test_validate_reports_mistakes():
    problems = config.validate(settings({"AAO_ACCOUNT_c_work_TIER": "gold", "AAO_ACCOUNT_c_work_HOSTS": "nowhere",
                                         "AAO_E2E_MODES": "native,telepathy"}))
    assert any("TIER" in p for p in problems) and any("nowhere" in p for p in problems) and any("telepathy" in p for p in problems)


def test_launch_spec_per_mechanism(monkeypatch):
    by_id = {a.id: a for a in config.accounts(settings())}
    env, args = accounts.launch_spec(by_id["c_work"])
    assert env["CLAUDE_CONFIG_DIR"].endswith(".claude-work") and args == []
    assert accounts.launch_spec(by_id["h_prof"]) == ({}, ["-p", "coder"])
    monkeypatch.setenv("CURSOR_KEY_2", "k-123")
    assert accounts.launch_spec(by_id["cur_key"]) == ({"CURSOR_API_KEY": "k-123"}, [])
    assert accounts.launch_spec(by_id["c_paid"]) == ({}, [])  # default login


def test_choose_prefers_free_then_priority_and_skips_exhausted():
    s = settings()
    chosen, _ = accounts.choose("claude", "local", quotas=[], settings=s)
    assert chosen.id == "c_free"
    exhausted = [{"provider": "claude", "aao_account": "c_free", "windows": [{"remaining_pct": 3}]}]
    chosen, reasons = accounts.choose("claude", "local", quotas=exhausted, settings=s)
    assert chosen.id == "c_paid" and any("3%" in r for r in reasons)
    chosen, _ = accounts.choose("claude", "box", quotas=[], settings=s)
    assert chosen.id == "c_paid"  # only account available on that host


def test_build_command_injects_account_and_permission_args():
    by_id = {a.id: a for a in config.accounts(settings())}
    argv, env = drivers.build_command("hermes", "hi there", by_id["h_prof"], permission="yolo")
    # --yolo after the subcommand, never between -q and its value
    assert argv == ["hermes", "-p", "coder", "chat", "--yolo", "-q", "hi there"] and env == {}
    argv, env = drivers.build_command("claude", "hi", by_id["c_work"], permission="auto")
    assert argv == ["claude", "--permission-mode", "auto", "-p", "hi"] and "CLAUDE_CONFIG_DIR" in env
    argv, _ = drivers.build_command("codex", "hi", permission="yolo")
    assert argv == ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "hi"]
    argv, _ = drivers.build_command("opencode", "hi", permission="auto")
    assert argv == ["opencode", "run", "--auto", "hi"]


def test_permission_mode_is_validated():
    import pytest
    with pytest.raises(ValueError):
        drivers.permission_args("claude", "reckless")


def test_model_args_go_after_the_subcommand():
    import pytest
    argv, _ = drivers.build_command("hermes", "hi", permission="auto", model="vendor/free-model:free")
    assert argv[:2] == ["hermes", "chat"]
    assert argv[argv.index("-m") + 1] == "vendor/free-model:free" and argv[-2:] == ["-q", "hi"]
    assert "-m" not in drivers.build_command("hermes", "hi", permission="auto")[0]
    assert "--provider" not in argv  # no provider given: the CLI's configured one is used
    argv, _ = drivers.build_command("hermes", "hi", model="openrouter:baidu/ernie-5.5-flash-preview:free")
    assert argv[argv.index("--provider") + 1] == "openrouter"
    assert argv[argv.index("-m") + 1] == "baidu/ernie-5.5-flash-preview:free"
    with pytest.raises(ValueError):  # no model_args in agents.json
        drivers.build_command("kiro", "hi", model="x")


def test_startup_forms_match_literal_screen_text():
    trust = "Quick safety check ...\n   No, exit\n ❯ Yes, I trust this folder\n Enter to confirm"
    form = drivers.match_startup_form("claude", trust)
    assert form["name"] == "folder-trust" and form["keys"] == ["down", "enter"]
    assert drivers.match_startup_form("claude", trust, trust_folders=False) is None
    assert drivers.match_startup_form("codex", "Update available · 0.1 → 0.2")["keys"] == ["esc"]
    assert drivers.match_startup_form("claude", "Please log in with your account") is None


def test_check_prompt_never_contains_its_answer():
    for _ in range(50):
        prompt, expect = drivers.check_prompt()
        assert re.search(expect, prompt) is None
        total = int(re.findall(r"\d+", expect)[-1])
        assert re.search(expect, f"AAO{total}") and re.search(expect, f"answer: {total}")
        assert not re.search(expect, f"{total}1")
