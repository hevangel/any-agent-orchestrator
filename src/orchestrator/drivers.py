"""Send one prompt to a CLI agent in a given run mode and return its output.

Modes (spec/machines.md):
  native        subprocess on this computer
  docker        `docker compose run --rm -T agents <cmd>` (credentials from the compose mounts)
  herdr         a throwaway herdr workspace on this computer; the headless command runs in its pane
  herdr-agent   a herdr pane running the agent interactively: `herdr agent start/prompt/wait/read`
  ssh           `ssh <target> bash -lc '<cmd>'` on a remote Linux/macOS machine
  herdr-remote  `herdr` mode against a saved herdr machine (`herdr --machine <label> ...`)

Every mode builds the command from the agent's `headless` template in agents.json, the
account's launch spec (accounts.launch_spec) and the permission mode, so a test exercises the
same path dispatch uses.

Everything here is a deterministic procedure: herdr is driven by fixed command sequences, and
interactive startup screens are recognised by literal text from agents.json (`startup_forms`)
and answered with that entry's fixed keys. No model is asked what to do; an unrecognised screen
fails with its text.

Permission modes (AAO_PERMISSION_MODE, agents.json `permission`):
  auto  the CLI's own automatic-approval mode (Claude `--permission-mode auto`, Codex
        `--approve-for-me`, ...) and folder trust for this launch
  yolo  skip every permission check (`--dangerously-skip-permissions`, `--yolo`, ...)
"""

import os

import random
import re
import shlex
import time
from dataclasses import dataclass

from orchestrator.accounts import launch_spec
from orchestrator.config import Settings, catalog
from orchestrator.machines import SSH_OPTIONS, _json, _run
from orchestrator.state import ROOT

PERMISSION_MODES = ("auto", "yolo")
STARTUP_ATTEMPTS = 4          # startup screens answered before giving up (update, trust, consent, ...)
STARTUP_POLL_SECONDS = 20


def permission_mode(mode=None):
    mode = mode or Settings().get("AAO_PERMISSION_MODE", "auto")
    if mode not in PERMISSION_MODES:
        raise ValueError(f"AAO_PERMISSION_MODE must be one of {PERMISSION_MODES}, not {mode!r}")
    return mode


def permission_args(cli, mode=None):
    return list((catalog()[cli].get("permission") or {}).get(permission_mode(mode), []))


@dataclass
class Result:
    ok: bool
    output: str
    error: str | None
    seconds: float
    command: str


def check_prompt():
    """A prompt whose expected answer never appears in the prompt itself.

    Terminal-reading modes see the echoed command line, so matching the prompt text would pass
    even if the agent never answered.
    """
    a, b = random.randint(1000, 4999), random.randint(1000, 4999)
    prompt = (f"What is {a} plus {b}? Reply with only the letters AAO immediately followed by the sum, "
              f"no spaces and nothing else. Do not run any tools.")
    # Some models drop the AAO prefix; the bare sum still proves an answer because it never
    # appears in the prompt. Portable regex (Python, JS, herdr): no lookbehind.
    return prompt, rf"(?:AAO|\b){a + b}\b"


def split_model(model):
    """`provider:model` -> (provider, model). Only a colon before the first slash names a provider,
    so OpenRouter ids such as `baidu/ernie-5.5-flash-preview:free` stay whole."""
    head, sep, rest = model.partition(":")
    if sep and "/" not in head and rest:
        return head, rest
    return None, model


def model_args(cli, model=None):
    """`agents.json` `model_args` with {provider} and {model} filled in; [] when no model is asked for.

    Without a provider, the `{provider}` part and the flag before it are left out, so the CLI's
    configured provider is used."""
    if not model:
        return []
    template = catalog()[cli].get("model_args")
    if not template:
        raise ValueError(f"{cli} has no model_args in agents.json, so a model cannot be chosen")
    provider, name = split_model(model)
    out = []
    for part in template:
        if "{provider}" in part:
            if not provider:
                if out and out[-1].startswith("-"):
                    out.pop()
                continue
            part = part.replace("{provider}", provider)
        out.append(part.replace("{model}", name))
    return out


def build_command(cli, prompt, account=None, permission=None, model=None):
    """argv + env for a one-shot run.

    Account args go right after the binary (Hermes `-p <profile>` is global); permission and model
    flags go before the first option of the template, i.e. after any subcommand (`codex exec`,
    `hermes chat`), so they never land between an option and its value (`hermes chat -q <prompt>`).
    """
    meta = catalog()[cli]
    if not meta.get("headless"):
        raise ValueError(f"{cli} has no headless command in agents.json")
    env, extra = launch_spec(account, meta) if account else ({}, [])
    template = meta["headless"]
    first_option = next((i for i, part in enumerate(template) if i and part.startswith("-")),
                        template.index("{prompt}"))
    template = (template[:first_option] + permission_args(cli, permission) + model_args(cli, model)
                + template[first_option:])
    argv = [part.replace("{prompt}", prompt) for part in template]
    return argv[:1] + extra + argv[1:], env


def _quote(arg):
    """Quote for both POSIX shells and PowerShell: single quotes, and the prompt has none."""
    if re.fullmatch(r"[A-Za-z0-9_./:=-]+", arg):
        return arg
    if "'" in arg:
        raise ValueError("arguments with single quotes are not supported by the pane modes")
    return f"'{arg}'"


def _line(argv):
    return " ".join(_quote(a) for a in argv)


def _timed(fn):
    start = time.monotonic()
    ok, out, err, command = fn()
    return Result(ok, out or "", err, round(time.monotonic() - start, 1), command)


# ------------------------------------------------------------------ modes

def run_native(argv, env, timeout):
    full_env = {**os.environ, **env}
    return _timed(lambda: (*_run(argv, timeout=timeout, env=full_env), _line(argv)))


def run_docker(argv, env, timeout, service="agents", cwd=None):
    """`cwd` (a host folder) is mounted at /task and used as the working directory instead of the
    compose WORKSPACE; agents that snapshot their folder (OpenCode) are slow on a large bind mount."""
    flags = [x for k, v in env.items() for x in ("-e", f"{k}={v}")]
    if cwd:
        flags += ["-v", f"{os.path.abspath(cwd)}:/task", "-w", "/task"]
    compose = str(ROOT / "docker-compose.yml")  # works from any cwd, not only the repo root
    cmd = ["docker", "compose", "-f", compose, "run", "--rm", "-T", *flags, service, *argv]
    return _timed(lambda: (*_run(cmd, timeout=timeout), _line(cmd)))


def run_ssh(target, argv, env, timeout):
    exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    remote = f"{exports} {shlex.join(argv)}".strip()
    cmd = ["ssh", *SSH_OPTIONS, target, f"bash -lc {shlex.quote(remote)}"]
    return _timed(lambda: (*_run(cmd, timeout=timeout), f"ssh {target} {remote}"))


def _herdr(machine, *args, timeout=60):
    """Run herdr; return (ok, parsed JSON or {"text": raw output}, error). `pane read` prints plain text."""
    base = ["herdr"] + (["--machine", machine] if machine else [])
    ok, out, err = _run(base + list(args), timeout=timeout)
    if not ok:
        return False, None, err
    parsed = _json(out)
    return True, parsed if isinstance(parsed, dict) else {"result": {"text": out}}, None


def _text(data):
    result = (data or {}).get("result") or {}
    return result.get("text") or (result.get("read") or {}).get("text") or ""


def _workspace(machine, env, cwd):
    args = ["workspace", "create", "--no-focus", "--label", f"aao-e2e-{random.randint(1000, 9999)}"]
    if cwd:
        args += ["--cwd", cwd]
    for k, v in env.items():
        args += ["--env", f"{k}={v}"]
    ok, data, err = _herdr(machine, *args)
    if not ok or not data:
        return None, None, err or "workspace create returned nothing"
    result = data.get("result") or {}
    return result["workspace"]["workspace_id"], result["root_pane"]["pane_id"], None


def run_herdr(argv, env, timeout, expect, machine=None, cwd=None):
    """Run the headless command in a fresh herdr pane and wait for the expected text in its output."""
    def go():
        workspace, pane, err = _workspace(machine, env, cwd)
        if err:
            return False, "", err, "herdr workspace create"
        try:
            ok, _, err = _herdr(machine, "pane", "run", pane, _line(argv))
            if not ok:
                return False, "", err, _line(argv)
            ok, data, err = _herdr(machine, "pane", "wait-output", pane, "--regex", expect,
                                   "--timeout", str(timeout * 1000), timeout=timeout + 15)
            text = _text(data)
            if not text:
                _, read, _ = _herdr(machine, "pane", "read", pane, "--lines", "60")
                text = _text(read)
            return ok, text, err, f"herdr{' --machine ' + machine if machine else ''} pane run {_line(argv)}"
        finally:
            _herdr(machine, "workspace", "close", workspace)
    return _timed(go)


def _agent_status(machine, name):
    _, data, _ = _herdr(machine, "agent", "get", name)
    return (((data or {}).get("result") or {}).get("agent") or {}).get("agent_status")


def match_startup_form(cli, screen, trust_folders=True):
    """The agents.json startup form shown on `screen`, or None. Pure function: literal text match."""
    for form in catalog()[cli].get("startup_forms") or []:
        if form["match"] in screen:
            if form["name"] == "folder-trust" and not trust_folders:
                return None
            return form
    return None


def clear_startup_forms(cli, machine, name, pane, trust_folders=True):
    """Answer startup screens until the agent is idle. Returns (ok, answered form names, last screen)."""
    answered = []
    screen = ""
    for _ in range(STARTUP_ATTEMPTS):
        if _agent_status(machine, name) == "idle":
            return True, answered, screen
        _, read, _ = _herdr(machine, "pane", "read", pane, "--source", "visible")
        screen = _text(read)
        form = match_startup_form(cli, screen, trust_folders)
        if form is not None:
            _herdr(machine, "pane", "send-keys", pane, *form["keys"])
            answered.append(form["name"])
        # No known form: usually still loading (herdr can report "blocked" during a splash screen).
        # Either way, give the agent time to reach idle before reading the screen again.
        deadline = time.monotonic() + STARTUP_POLL_SECONDS
        while time.monotonic() < deadline:
            status = _agent_status(machine, name)
            if status == "idle":
                return True, answered, screen
            if status == "blocked":
                break
            time.sleep(1)
    return _agent_status(machine, name) == "idle", answered, screen


def start_interactive(cli, machine, pane, extra_args, name=None, trust_folders=True):
    """Start `cli` interactively in `pane` and get it to idle. Returns (name, error, screen, answered)."""
    kind = catalog()[cli].get("herdr_kind")
    if not kind:
        return None, f"herdr has no agent kind for {cli}", "", []
    name = name or f"aao-{cli}-{random.randint(1000, 9999)}"
    ok, _, err = _herdr(machine, "agent", "start", name, "--kind", kind, "--pane", pane,
                        *(["--", *extra_args] if extra_args else []), timeout=90)
    if ok:
        return name, None, "", []
    if "blocked" not in (err or ""):
        return None, err, "", []
    cleared, answered, screen = clear_startup_forms(cli, machine, name, pane, trust_folders)
    if not cleared:
        return None, (f"stopped at an unrecognised startup screen after answering {answered or 'nothing'}; "
                      "add it to startup_forms in agents.json"), screen, answered
    return name, None, screen, answered


def run_herdr_agent(cli, prompt, env, extra_args, timeout, expect, machine=None, cwd=None, trust_folders=True):
    """Drive the agent's interactive TUI through herdr's agent API (the orchestration path)."""
    if not catalog()[cli].get("herdr_kind"):
        return Result(False, "", f"herdr has no agent kind for {cli}", 0, "")

    def go():
        workspace, pane, err = _workspace(machine, env, cwd)
        if err:
            return False, "", err, "herdr workspace create"
        try:
            name, err, screen, answered = start_interactive(cli, machine, pane, extra_args, trust_folders=trust_folders)
            command = f"herdr agent start {cli} {' '.join(extra_args)}".strip()
            if err:
                return False, screen, err, command
            _herdr(machine, "agent", "wait", name, "--until", "idle", "--timeout", "60000", timeout=75)
            ok, _, err = _herdr(machine, "agent", "prompt", name, prompt, "--wait", "--until", "idle",
                                "--until", "done", "--until", "blocked", timeout=timeout)
            if not ok and "agent_prompt_stalled" in (err or ""):
                # The text was typed but no working state was seen: either the reply was too quick to
                # observe, or the submit key was swallowed while the TUI was redrawing. Press Enter once
                # if the prompt is still sitting in the input box, then wait for idle.
                _, read, _ = _herdr(machine, "agent", "read", name, "--source", "visible")
                if re.search(expect, _text(read)) is None and prompt[:40] in _text(read):
                    _herdr(machine, "pane", "send-keys", pane, "enter")
                ok, _, err = _herdr(machine, "agent", "wait", name, "--until", "idle", "--until", "done",
                                    "--timeout", str(timeout * 1000), timeout=timeout + 15)
                time.sleep(2)
            _, read, _ = _herdr(machine, "agent", "read", name, "--source", "recent-unwrapped", "--lines", "120")
            text = _text(read)
            note = f" (answered startup screens: {', '.join(answered)})" if answered else ""
            return ok and re.search(expect, text) is not None, text, err, command + note
        finally:
            _herdr(machine, "workspace", "close", workspace)
    return _timed(go)


def send(cli, mode, prompt, expect, account=None, host=None, timeout=180, cwd=None, permission=None,
         model=None):
    """Dispatch one prompt; `host` is a config.Host for ssh / herdr-remote; `model` uses `model_args`."""
    argv, env = build_command(cli, prompt, account, permission, model)
    trust_folders = Settings().get("AAO_TRUST_FOLDERS", "1") != "0"
    if mode == "native":
        result = run_native(argv, env, timeout)
    elif mode == "docker":
        result = run_docker(argv, env, timeout, cwd=cwd)
    elif mode == "ssh":
        result = run_ssh(host.ssh, argv, env, timeout)
    elif mode == "herdr":
        result = run_herdr(argv, env, timeout, expect, cwd=cwd)
    elif mode == "herdr-remote":
        result = run_herdr(argv, env, timeout, expect, machine=host.herdr)
    elif mode == "herdr-agent":
        _, extra = launch_spec(account) if account else ({}, [])
        result = run_herdr_agent(cli, prompt, env, extra + permission_args(cli, permission) + model_args(cli, model),
                                 timeout, expect,
                                 cwd=cwd, trust_folders=trust_folders)
    else:
        raise ValueError(f"unknown mode {mode}")
    if result.ok and not re.search(expect, result.output):
        # The terminal modes already matched; subprocess modes must check the answer here.
        result.ok, result.error = False, f"answer {expect} not found in output"
    return result
