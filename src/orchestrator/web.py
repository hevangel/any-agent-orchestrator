"""Drive AI chat web apps with playwright-cli, one persistent browser profile per site.

    uv run aao web login grok          # opens a visible browser; you log in; the profile is saved
    uv run aao web login --all
    uv run aao web status              # which saved profiles are still logged in
    uv run aao web ask chatgpt --prompt "..." [--expect REGEX] [--chat <id|url|title>]
    (chat list, pins and groups: web_chats.py, `aao chats`)

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
    # Studied 2026-11: Enter sends and the URL becomes /chat/<id>; the reply is the last
    # assistant-message, data-is-streaming="true" (plus a "Stop response" button) while it is written.
    "claude": {"url": "https://claude.ai/new",
               "composer": ["[role='textbox'][aria-label='Write your prompt to Claude']",
                            "div.ProseMirror[contenteditable='true']"],
               "send": "button[aria-label='Send message']",
               "user": "[data-testid='user-message']",
               "assistant": "[data-testid='assistant-message']",
               "busy": ["[data-testid='assistant-message'][data-is-streaming='true']",
                        "button[aria-label='Stop response']"],
               "strip": [".sr-only", "[data-testid='message-actions']"],
               "chat": True},
    "gemini": {"url": "https://gemini.google.com/app", "composer": ["rich-textarea div[role='textbox']",
                                                                   "div[contenteditable='true'][role='textbox']"], "chat": True},
    "grok": {"url": "https://grok.com/", "composer": ["textarea[placeholder*='Ask']", "textarea",
                                                      "div[contenteditable='true']"], "chat": True},
    "muse": {"url": "https://muse.ai/", "composer": ["textarea[placeholder='Message']", "textarea",
                                                     "div[contenteditable='true']"], "chat": True},
    "zai": {"url": "https://chat.z.ai/", "composer": ["textarea", "div[contenteditable='true']"], "chat": True},
    "codex-cloud": {"url": "https://chatgpt.com/codex", "composer": ["#prompt-textarea", "textarea",
                                                                    "div[contenteditable='true']"], "chat": False},
    # sessions are driven by web_code.py (`aao code`); here only the login check. Same site and
    # account as "claude", so it shares that profile and browser session.
    "claude-code-web": {"url": "https://claude.ai/code", "profile": "claude",
                        "composer": ["[data-testid='code-prompt-input']",
                                                                       "[role='textbox'][aria-label='Prompt']"], "chat": False},
}
LOGIN_URL = r"login|signin|sign-in|sign_in|/auth|accounts\.google\.com|oauth"

PAGE_SCRIPT = r"""async page => {
  const cfg = __CFG__;
  // passive (login wait): only look at the page; never navigate, click or type while a human logs in
  if (!cfg.passive && !page.url().startsWith(cfg.url)) await page.goto(cfg.url, { waitUntil: 'domcontentloaded' });
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

  // ---- deterministic turn: no model decides anything here, every step is a fixed selector check
  const started = Date.now();
  const count = sel => sel ? page.locator(sel).count() : Promise.resolve(0);
  const anyVisible = async sels => {
    for (const s of sels || []) if (await page.locator(s).first().isVisible().catch(() => false)) return true;
    return false;
  };
  const lastReply = () => page.evaluate(({ sel, strip }) => {
    const all = document.querySelectorAll(sel);
    if (!all.length) return '';
    const copy = all[all.length - 1].cloneNode(true);
    for (const s of strip || []) copy.querySelectorAll(s).forEach(e => e.remove());
    return (copy.innerText || copy.textContent || '').trim();
  }, { sel: cfg.assistant, strip: cfg.strip });

  const repliesBefore = await count(cfg.assistant);
  const usersBefore = await count(cfg.user);
  await composer.click();
  await page.keyboard.insertText(cfg.prompt);
  await page.keyboard.press('Enter');

  // 1. the message must leave the composer: a new user turn or a URL change; else click Send once
  const sent = async () => (cfg.user && await count(cfg.user) > usersBefore) || page.url() !== url;
  let ok = false;
  for (let i = 0; i < 20 && !(ok = await sent()); i++) await page.waitForTimeout(500);
  if (!ok && cfg.send) {
    await page.locator(cfg.send).first().click().catch(() => {});
    for (let i = 0; i < 20 && !(ok = await sent()); i++) await page.waitForTimeout(500);
  }
  if (!ok) return JSON.stringify({ loggedIn: true, ok: false, url: page.url(), stage: 'send', reason: 'message was not sent' });

  // 2. wait for the reply: a new assistant turn, no busy marker, and text unchanged over two polls
  const deadline = started + cfg.timeoutMs;
  let reply = '', previous = null, stage = 'reply';
  while (Date.now() < deadline) {
    await page.waitForTimeout(1000);
    if (!cfg.assistant) {  // site not studied yet: fall back to the whole page text
      reply = await page.locator('body').innerText();
      if (new RegExp(cfg.expect).test(reply)) return JSON.stringify({ loggedIn: true, ok: true, url: page.url(), mode: 'body' });
      continue;
    }
    if (await count(cfg.assistant) <= repliesBefore) { stage = 'waiting for reply'; continue; }
    if (await anyVisible(cfg.busy)) { stage = 'streaming'; previous = null; continue; }
    reply = await lastReply();
    if (reply && reply === previous) {
      const matched = cfg.expect ? new RegExp(cfg.expect).test(reply) : true;
      return JSON.stringify({ loggedIn: true, ok: matched, url: page.url(), reply, seconds: (Date.now() - started) / 1000,
                              ...(matched ? {} : { reason: 'reply does not match ' + cfg.expect }) });
    }
    previous = reply; stage = 'settling';
  }
  return JSON.stringify({ loggedIn: true, ok: false, url: page.url(), stage, reason: 'timed out',
                          reply: cfg.assistant ? reply : undefined, tail: cfg.assistant ? undefined : reply.slice(-400) });
}"""


def _settings():
    return Settings()


def login_name(target):
    """Targets on the same site and account share one login: one profile and one browser session
    (Chrome cannot open a profile twice). E.g. claude-code-web uses the claude login."""
    return TARGETS[target].get("profile", target)


def profile_dir(target, settings=None):
    s = settings or _settings()
    base = os.path.expanduser(s.get("AAO_WEB_PROFILE_DIR", "~/.aao/browser-profiles"))
    path = Path(base) / login_name(target)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session(target):
    return f"-s=aao-web-{login_name(target)}"


def _pw(target, *args, timeout=120):
    return _run(["playwright-cli", _session(target), *args], timeout=timeout)


def open_sessions():
    """Names of the playwright-cli browser sessions that are open (`playwright-cli list`)."""
    ok, out, _ = _run(["playwright-cli", "list"], timeout=30)
    names, current = set(), None
    for line in (out or "").splitlines():
        m = re.match(r"^- (\S+):\s*$", line)
        if m:
            current = m.group(1)
        elif current and re.match(r"^\s+- status: open\s*$", line):
            names.add(current)
    return names


def is_open(target):
    return _session(target)[3:] in open_sessions()


def open_target(target, headed=None, settings=None):
    """Open the target's browser, or reuse it when it is already open (the page is left where it is;
    every script navigates to what it needs)."""
    s = settings or _settings()
    cfg = TARGETS[target]
    if is_open(target):
        return True, "reused", ""
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


def release(target, settings=None):
    """End of one command: keep the browser open for the next one (default), or close it when
    AAO_WEB_KEEP_OPEN=0. `aao web close` closes it explicitly."""
    s = settings or _settings()
    if s.get("AAO_WEB_KEEP_OPEN", "1") == "0":
        close_target(target)


def _run_page(target, prompt=None, expect=None, timeout_s=120, settle_ms=4000, passive=False, url=None):
    """One page check or prompt turn. `url` opens that page (e.g. an existing chat) instead of a new chat."""
    cfg = TARGETS[target]
    if passive and prompt:
        raise ValueError("a passive page check cannot send a prompt")
    payload = {"url": url or cfg["url"], "composer": cfg["composer"], "loginUrl": LOGIN_URL, "prompt": prompt,
               "expect": expect, "timeoutMs": timeout_s * 1000, "settleMs": settle_ms, "passive": passive,
               **{k: cfg.get(k) for k in ("send", "user", "assistant", "busy", "strip")}}
    return run_script(target, PAGE_SCRIPT.replace("__CFG__", json.dumps(payload)), timeout_s + 60)


def run_script(target, script, timeout_s=180):
    """Run a page function (`async page => JSON string`) in the target's open session; return its dict."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    try:
        ok, out, err = _pw(target, "run-code", "--filename", path, "--raw", timeout=timeout_s)
    finally:
        os.unlink(path)
    if not ok:
        return {"loggedIn": None, "error": (err or out)[-600:]}
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
        release(target)


def login(target, wait_minutes=15, settings=None):
    """Open a visible browser on the target and wait until a human has logged in."""
    ok, _, err = open_target(target, headed=True, settings=settings)
    if not ok:
        return {"target": target, "loggedIn": False, "error": err}
    print(f"[{target}] log in in the browser window (profile {profile_dir(target, settings)}); waiting up to {wait_minutes} min ...")
    deadline = time.time() + wait_minutes * 60
    try:
        while time.time() < deadline:
            # passive: a page reload or redirect here would wipe what the human is typing
            state = _run_page(target, settle_ms=500, passive=True)
            if state.get("loggedIn"):
                print(f"[{target}] logged in; profile saved")
                return {"target": target, **state}
            time.sleep(5)
        return {"target": target, "loggedIn": False, "error": "timed out waiting for login"}
    finally:
        release(target)


def ask(target, prompt, expect, timeout_s=120, settings=None, screenshot_path=None, chat=None):
    """Send one prompt; `chat` (id, URL or exact title) continues that chat instead of starting a new one."""
    ok, _, err = open_target(target, settings=settings)
    if not ok:
        return {"target": target, "ok": False, "error": err}
    try:
        url = None
        if chat:
            from orchestrator import web_chats
            found = web_chats.resolve(target, chat)
            if not found.get("url"):
                return {"target": target, "ok": False, **found}
            url = found["url"]
        return {"target": target, **_run_page(target, prompt, expect, timeout_s, url=url)}
    finally:
        if screenshot_path:
            screenshot(target, screenshot_path)
        release(target)
