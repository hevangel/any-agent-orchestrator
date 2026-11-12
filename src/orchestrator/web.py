"""Drive AI chat web apps with playwright-cli, one persistent browser profile per site.

    uv run aao web login grok          # opens a visible browser; you log in; the profile is saved
    uv run aao web login --all
    uv run aao web status              # which saved profiles are still logged in
    uv run aao web ask chatgpt "..."   # send one prompt, print the reply check

Profiles live in AAO_WEB_PROFILE_DIR (default ~/.aao/browser-profiles/<target>), outside the
repository: they hold session cookies. Logins are always done by a human in the visible window;
this module never types credentials.
"""

import json
import os
import re
import tempfile
import time
from pathlib import Path

from orchestrator.config import Settings
from orchestrator.machines import _run

# composer: selector hints in priority order (spec/web_interfaces.md); chat: whether a prompt
# test is meaningful (remote Codex / Claude Code create cloud tasks, so they are only login-checked).
TARGETS = {
    "chatgpt": {"url": "https://chatgpt.com/", "composer": ["#prompt-textarea", "div[contenteditable='true']"], "chat": True},
    "claude": {"url": "https://claude.ai/new", "composer": ["div.ProseMirror[contenteditable='true']",
                                                           "[contenteditable='true'][role='textbox']"], "chat": True},
    "gemini": {"url": "https://gemini.google.com/app", "composer": ["rich-textarea div[role='textbox']",
                                                                   "div[contenteditable='true'][role='textbox']"], "chat": True},
    "grok": {"url": "https://grok.com/", "composer": ["textarea[placeholder*='Ask']", "textarea",
                                                      "div[contenteditable='true']"], "chat": True},
    "muse": {"url": "https://muse.ai/", "composer": ["textarea[placeholder='Message']", "textarea",
                                                     "div[contenteditable='true']"], "chat": True},
    "zai": {"url": "https://chat.z.ai/", "composer": ["textarea", "div[contenteditable='true']"], "chat": True},
    "codex-cloud": {"url": "https://chatgpt.com/codex", "composer": ["#prompt-textarea", "textarea",
                                                                    "div[contenteditable='true']"], "chat": False},
    "claude-code-web": {"url": "https://claude.ai/code", "composer": ["textarea", "div[contenteditable='true']"], "chat": False},
}
LOGIN_URL = r"login|signin|sign-in|sign_in|/auth|accounts\.google\.com|oauth"

PAGE_SCRIPT = r"""async page => {
  const cfg = __CFG__;
  if (!page.url().startsWith(cfg.url)) await page.goto(cfg.url, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(cfg.settleMs);
  const url = page.url();
  if (new RegExp(cfg.loginUrl, 'i').test(url)) return JSON.stringify({ loggedIn: false, url, reason: 'login page' });
  let composer = null;
  for (const sel of cfg.composer) {
    const loc = page.locator(sel).first();
    if (await loc.isVisible().catch(() => false)) { composer = loc; break; }
  }
  if (!composer) return JSON.stringify({ loggedIn: false, url, reason: 'no chat box (logged out or page changed)' });
  // Several sites let guests chat; a visible "Sign in" / "Log in" control means we are a guest.
  const signIn = /^\s*(sign in|log in|login|sign up|get started)\s*$/i;
  const guest = await page.getByRole('button', { name: signIn }).or(page.getByRole('link', { name: signIn }))
    .first().isVisible().catch(() => false);
  if (guest) return JSON.stringify({ loggedIn: false, guest: true, url, reason: 'guest session (sign-in button visible)' });
  if (!cfg.prompt) return JSON.stringify({ loggedIn: true, url });
  await composer.click();
  await page.keyboard.insertText(cfg.prompt);
  await page.keyboard.press('Enter');
  const deadline = Date.now() + cfg.timeoutMs;
  let text = '';
  while (Date.now() < deadline) {
    await page.waitForTimeout(2000);
    text = await page.locator('body').innerText();
    if (new RegExp(cfg.expect).test(text)) return JSON.stringify({ loggedIn: true, ok: true, url });
  }
  return JSON.stringify({ loggedIn: true, ok: false, url, tail: text.slice(-400) });
}"""


def _settings():
    return Settings()


def profile_dir(target, settings=None):
    s = settings or _settings()
    base = os.path.expanduser(s.get("AAO_WEB_PROFILE_DIR", "~/.aao/browser-profiles"))
    path = Path(base) / target
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session(target):
    return f"-s=aao-web-{target}"


def _pw(target, *args, timeout=120):
    return _run(["playwright-cli", _session(target), *args], timeout=timeout)


def open_target(target, headed=None, settings=None):
    s = settings or _settings()
    cfg = TARGETS[target]
    headed = s.get("AAO_WEB_HEADED", "1") != "0" if headed is None else headed
    args = ["open", "--persistent", "--profile", str(profile_dir(target, s))]
    browser = s.get("AAO_WEB_BROWSER")  # e.g. chrome: Google sign-in rejects automation-flagged Chromium
    if browser:
        args += ["--browser", browser]
    if headed:
        args.append("--headed")
    return _pw(target, *args, cfg["url"], timeout=120)


def close_target(target):
    return _pw(target, "close", timeout=60)


def _run_page(target, prompt=None, expect=None, timeout_s=120, settle_ms=4000):
    cfg = TARGETS[target]
    payload = {"url": cfg["url"], "composer": cfg["composer"], "loginUrl": LOGIN_URL, "prompt": prompt,
               "expect": expect, "timeoutMs": timeout_s * 1000, "settleMs": settle_ms}
    script = PAGE_SCRIPT.replace("__CFG__", json.dumps(payload))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    try:
        ok, out, err = _pw(target, "run-code", "--filename", path, "--raw", timeout=timeout_s + 60)
    finally:
        os.unlink(path)
    if not ok:
        return {"loggedIn": None, "error": err}
    # `--raw` prints the function's return value as JSON, i.e. our JSON string quoted once more.
    try:
        data = json.loads(out.strip())
        while isinstance(data, str):
            data = json.loads(data)
        return data if isinstance(data, dict) else {"loggedIn": None, "error": out[-300:]}
    except ValueError:
        match = re.search(r"\{.*\}", out, re.S)
        try:
            return json.loads(match.group(0)) if match else {"loggedIn": None, "error": out[-300:]}
        except ValueError:
            return {"loggedIn": None, "error": out[-300:]}


def screenshot(target, path):
    """Save the current page as PNG evidence (best effort)."""
    _pw(target, "screenshot", f"--filename={path}", timeout=60)


def status(target, settings=None, screenshot_path=None):
    ok, _, err = open_target(target, settings=settings)
    if not ok:
        return {"target": target, "loggedIn": None, "error": err}
    try:
        return {"target": target, **_run_page(target)}
    finally:
        if screenshot_path:
            screenshot(target, screenshot_path)
        close_target(target)


def login(target, wait_minutes=15, settings=None):
    """Open a visible browser on the target and wait until a human has logged in."""
    ok, _, err = open_target(target, headed=True, settings=settings)
    if not ok:
        return {"target": target, "loggedIn": False, "error": err}
    print(f"[{target}] log in in the browser window (profile {profile_dir(target, settings)}); waiting up to {wait_minutes} min ...")
    deadline = time.time() + wait_minutes * 60
    try:
        while time.time() < deadline:
            state = _run_page(target, settle_ms=1500)
            if state.get("loggedIn"):
                print(f"[{target}] logged in; profile saved")
                return {"target": target, **state}
            time.sleep(5)
        return {"target": target, "loggedIn": False, "error": "timed out waiting for login"}
    finally:
        close_target(target)


def ask(target, prompt, expect, timeout_s=120, settings=None, screenshot_path=None):
    ok, _, err = open_target(target, settings=settings)
    if not ok:
        return {"target": target, "ok": False, "error": err}
    try:
        return {"target": target, **_run_page(target, prompt, expect, timeout_s)}
    finally:
        if screenshot_path:
            screenshot(target, screenshot_path)
        close_target(target)
